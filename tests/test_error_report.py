"""Reporting an error by e-mail: what the report carries and where the hosts send it."""
import json
import platform
import re
import sys
import unittest
from unittest import mock
from urllib.parse import parse_qs, quote, urlparse

from support import ROOT, needs_node, run_ui_scripts

import app
import desktop
import error_report

PAGES = ("shell.js", "pdf.mjs", "message.js", "container.js")


def page_data(page):
    return json.loads(re.search(r"<script type=\"application/json\" data-zfo-page[^>]*>(.*?)</script>", page, re.S).group(1))


class MailLink(unittest.TestCase):
    def test_goes_only_to_the_project_address(self):
        link = urlparse(error_report.mail_link("Hlášení chyby", "Co se stalo:\nspadlo to &to=cizi@example.com"))
        self.assertEqual((link.scheme, link.path), ("mailto", "podpora@blanketa.cz"))
        fields = parse_qs(link.query)
        self.assertEqual(set(fields), {"subject", "body"})
        self.assertEqual(fields["body"], ["Co se stalo:\nspadlo to &to=cizi@example.com"])

    def test_refuses_anything_but_text(self):
        for subject, body in ((None, "x"), ("x", None), ("x", ["y"]), (5, "x")):
            with self.subTest(subject=subject, body=body):
                self.assertIsNone(error_report.mail_link(subject, body))

    def test_names_the_system_and_the_processor(self):
        description = error_report.system_description()
        expected_start = {"darwin": "macOS ", "win32": "Windows "}.get(sys.platform, "")
        self.assertTrue(description.startswith(expected_start), description)
        self.assertTrue(description.endswith(f"{platform.machine()})"), description)


class ReportOnEveryPage(unittest.TestCase):
    def test_page_tells_the_system_and_the_kind_of_document(self):
        data = page_data(app.render_empty_window())
        self.assertEqual(data["system"], error_report.system_description())
        self.assertEqual(data["document"], "prázdné okno")

    def test_every_page_opens_it_from_the_menu(self):
        for page in PAGES:
            with self.subTest(page=page):
                self.assertIn("'report-error': () => zfoReportError()", (ROOT / "ui" / page).read_text(encoding="utf-8"))

    def test_every_host_offers_it_in_the_menu(self):
        self.assertIn('formItem("Nahlásit chybu…", "report-error")', (ROOT / "macapp/main.swift").read_text(encoding="utf-8"))
        self.assertIn('page_action("Nahlásit chybu…", "report-error")', (ROOT / "desktop.py").read_text(encoding="utf-8"))

    def test_mac_host_writes_only_to_the_project_address(self):
        swift = (ROOT / "macapp/main.swift").read_text(encoding="utf-8")
        self.assertIn(f'static let address = "{error_report.ADDRESS}"', swift)
        self.assertIn("ErrorReport.mailLink(subject: subject, body: text)", swift)
        self.assertIn(f"const ADDRESS = '{error_report.ADDRESS}';", (ROOT / "ui/bridge.js").read_text(encoding="utf-8"))


class DesktopHostOpensTheMail(unittest.TestCase):
    def setUp(self):
        self.window = mock.Mock()
        self.window.get_current_url.return_value = "http://127.0.0.1:8123/"
        self.bridge = desktop.Bridge(mock.Mock(), mock.Mock(home="http://127.0.0.1:8123/", window=self.window))

    def test_opens_the_mail_program_with_the_report(self):
        with mock.patch.object(desktop, "open_with_system") as open_with_system:
            self.bridge.message({"action": "reportError", "subject": "Hlášení", "body": "text"})
        link = open_with_system.call_args.args[0]
        self.assertTrue(link.startswith("mailto:podpora@blanketa.cz?"), link)

    def test_ignores_a_report_without_text(self):
        with mock.patch.object(desktop, "open_with_system") as open_with_system:
            self.bridge.message({"action": "reportError", "subject": "Hlášení", "to": "cizi@example.com"})
        open_with_system.assert_not_called()

    def test_ignores_a_report_from_a_foreign_page(self):
        self.window.get_current_url.return_value = "https://evil.example/"
        with mock.patch.object(desktop, "open_with_system") as open_with_system:
            self.bridge.message({"action": "reportError", "subject": "Hlášení", "body": "text"})
        open_with_system.assert_not_called()


@needs_node
class ReportText(unittest.TestCase):
    PAGE = "document.querySelector = () => ({textContent: JSON.stringify({app_version: '1.2.3', system: 'macOS 27.2 (arm64)', document: 'PDF'})});"

    def run_report(self, script):
        return json.loads(run_ui_scripts(["bridge.js"], self.PAGE + script))

    def test_carries_version_system_document_and_the_last_errors(self):
        text = self.run_report("""
            for (let i = 1; i <= 7; i++) zfoReportError.record(`chyba ${i}`, 'http://127.0.0.1:1/pdf.mjs?v=1', i);
            console.log(JSON.stringify(zfoReportError.template()));""")
        self.assertIn("Blanketa: 1.2.3\nSystém: macOS 27.2 (arm64)\nDokument: PDF\n", text)
        self.assertEqual(re.findall(r"^- .*$", text, re.M), [f"- chyba {i} (pdf.mjs:{i})" for i in range(3, 8)])

    def test_error_text_loses_quoted_values_and_long_numbers(self):
        text = self.run_report("""
            zfoReportError.record('Neplatné datum "1. 2. 1985" v poli \\'Jan Novák\\' pro 9801011234 a „tajne.zfo“');
            console.log(JSON.stringify(zfoReportError.template()));""")
        self.assertIn("- Neplatné datum … v poli … pro … a …\n", text)
        for value in ("1985", "Novák", "9801011234", "tajne"):
            self.assertNotIn(value, text)

    def test_long_report_fits_the_mail_link_and_says_where_the_rest_is(self):
        body = self.run_report("console.log(JSON.stringify(zfoReportError.fitIntoMailLink('č'.repeat(3000))));")
        self.assertLessEqual(len(quote(body, safe="-_.!~*'()")), 1800)
        self.assertTrue(body.endswith("Celý je ve schránce – vložte ho sem místo tohoto.]"), body[-80:])
        self.assertEqual(self.run_report("console.log(JSON.stringify(zfoReportError.fitIntoMailLink('krátké')))"), "krátké")
