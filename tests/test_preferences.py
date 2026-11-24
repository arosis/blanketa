import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import ROOT  # noqa: F401

import crash_report
import preferences
import support_reminder
import time_stamps
import update_check


class WithOwnSettingsFolder(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        for patcher in (
            mock.patch.object(update_check.certstore, "config_dir", return_value=self.folder),
            mock.patch.object(crash_report, "settings_path", return_value=self.folder / crash_report.SETTINGS_FILE),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def stored(self, file_name):
        return json.loads((self.folder / file_name).read_text(encoding="utf-8"))


class Current(WithOwnSettingsFolder):
    def test_current_reports_defaults(self):
        self.assertEqual(preferences.current(), {
            "update_checks": True,
            "crash_offers": True,
            "support_reminder": True,
            "time_stamp": {"service": time_stamps.PUBLIC_SERVICE, "url": "", "user": "", "usable": True},
        })


class Change(WithOwnSettingsFolder):
    def test_each_option_changes_alone(self):
        for name in ("update_checks", "crash_offers", "support_reminder"):
            with self.subTest(name):
                expected = {**preferences.current(), name: False}
                self.assertEqual(preferences.change({name: False}), expected)
                preferences.change({name: True})

    def test_support_reminder_turned_on_keeps_last_shown(self):
        path = self.folder / support_reminder.SETTINGS_FILE
        path.write_text(json.dumps({"hidden": True, "last_shown": "2026-11-20"}), encoding="utf-8")
        preferences.change({"support_reminder": True})
        self.assertEqual(self.stored(support_reminder.SETTINGS_FILE), {"last_shown": "2026-11-20"})

    def test_own_service_address_is_checked_even_when_stamps_are_off(self):
        with self.assertRaises(ValueError):
            preferences.change({"time_stamp": {"service": "own", "url": "ftp://x", "user": ""}})
        self.assertFalse((self.folder / time_stamps.SETTINGS_FILE).exists())

    def test_nothing_is_written_when_one_part_is_refused(self):
        with self.assertRaises(ValueError):
            preferences.change({"update_checks": False, "time_stamp": {"service": "own", "url": "ftp://x", "user": ""}})
        self.assertTrue(update_check.enabled())

    def test_own_service_is_stored(self):
        changed = preferences.change({"time_stamp": {"service": "own", "url": "https://tsa.example", "user": "u"}})
        self.assertEqual(changed["time_stamp"], {"service": "own", "url": "https://tsa.example", "user": "u", "usable": True})

    def test_unknown_keys_and_wrong_types_are_refused(self):
        for wanted in ({"update_checks": "no"}, {"time_stamp": []}, {"x": 1}, {"time_stamp": {"url": 5}},
                       {"time_stamp": {"service": "other"}}, []):
            with self.subTest(wanted), self.assertRaises(ValueError):
                preferences.change(wanted)


class TimeStampRecord(WithOwnSettingsFolder):
    def test_enabled_and_service_writes_do_not_clobber_each_other(self):
        time_stamps.store_enabled(True)
        time_stamps.store_service("own", "https://tsa.example", "")
        self.assertEqual(self.stored(time_stamps.SETTINGS_FILE)["enabled"], True)
        self.assertEqual(self.stored(time_stamps.SETTINGS_FILE)["service"], "own")
        time_stamps.store_enabled(False)
        time_stamps.store_service("public", "", "")
        reverse = self.stored(time_stamps.SETTINGS_FILE)
        self.assertEqual((reverse["enabled"], reverse["service"]), (False, "public"))

    def test_usable(self):
        own = time_stamps.OWN_SERVICE
        self.assertTrue(time_stamps.usable({"service": own, "url": "https://tsa.example", "user": ""}))
        self.assertFalse(time_stamps.usable({"service": own, "url": "", "user": ""}))
        self.assertFalse(time_stamps.usable({"service": own, "url": "http://x", "user": "u"}))
        self.assertTrue(time_stamps.usable({"service": time_stamps.PUBLIC_SERVICE, "url": "", "user": ""}))


if __name__ == "__main__":
    unittest.main()
