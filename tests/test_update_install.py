import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import certstore
import update_check
import update_install


def running(executable, *, mac=False, windows=False, writable=True, env=None):
    patches = [
        mock.patch.object(update_install.sys, "frozen", True, create=True),
        mock.patch.object(update_install.sys, "executable", str(executable)),
        mock.patch.object(update_install.certstore, "IS_MAC", mac),
        mock.patch.object(update_install.certstore, "IS_WINDOWS", windows),
        mock.patch.object(update_install.os, "access", return_value=writable),
        mock.patch.dict(os.environ, env or {}),
    ]
    for patcher in patches:
        patcher.start()
    return patches


class WithPatches(unittest.TestCase):
    def run_as(self, *args, **kwargs):
        for patcher in running(*args, **kwargs):
            self.addCleanup(patcher.stop)


class UpdatesFolder(unittest.TestCase):
    def test_lives_in_the_config_folder(self):
        self.assertEqual(update_install.updates_dir(), certstore.config_dir() / "updates")


class NotFrozen(unittest.TestCase):
    def test_source_run_is_not_installable(self):
        self.assertIsNone(update_install.target())
        self.assertIsNone(update_install.not_replaceable_reason())


class Mac(WithPatches):
    def app_executable(self, folder):
        return Path(folder) / "Blanketa.app/Contents/Resources/server/blanketa-server"

    def test_app_in_applications_is_installable(self):
        self.run_as(self.app_executable("/Applications"), mac=True)
        self.assertEqual(
            update_install.target(),
            {"kind": "mac-app", "app": Path("/Applications/Blanketa.app"), "all_users": False},
        )
        self.assertIsNone(update_install.not_replaceable_reason())

    def test_dmg_and_translocated_app_give_the_move_reason(self):
        for folder in ("/Volumes/Blanketa", "/private/var/folders/x/AppTranslocation/ABC/d"):
            with self.subTest(folder=folder):
                self.run_as(self.app_executable(folder), mac=True)
                self.assertIsNone(update_install.target())
                self.assertEqual(update_install.not_replaceable_reason(), "move-to-applications")

    def test_shallow_path_is_not_replaceable(self):
        self.run_as("/blanketa", mac=True)
        self.assertIsNone(update_install.target())
        self.assertEqual(update_install.not_replaceable_reason(), "move-to-applications")

    def test_unwritable_parent_gives_the_move_reason(self):
        self.run_as(self.app_executable("/Applications"), mac=True, writable=False)
        self.assertIsNone(update_install.target())
        self.assertEqual(update_install.not_replaceable_reason(), "move-to-applications")


class Windows(WithPatches):
    def test_setup_install_per_user(self):
        self.run_as("C:/Users/x/AppData/Local/Programs/Blanketa/Blanketa.exe", windows=True, env={"ProgramFiles": "/pf"})
        with mock.patch.object(update_check, "portable_windows", return_value=False):
            found = update_install.target()
        self.assertEqual(found["kind"], "windows-setup")
        self.assertFalse(found["all_users"])

    def test_setup_install_in_program_files_is_for_all_users(self):
        self.run_as("/pf/Blanketa/Blanketa.exe", windows=True, env={"ProgramFiles": "/pf"})
        with mock.patch.object(update_check, "portable_windows", return_value=False):
            found = update_install.target()
        self.assertEqual((found["kind"], found["all_users"], found["app"]), ("windows-setup", True, Path("/pf/Blanketa")))

    def test_portable_folder(self):
        self.run_as("/data/Blanketa/Blanketa.exe", windows=True, env={"ProgramFiles": "/pf"})
        with mock.patch.object(update_check, "portable_windows", return_value=True):
            found = update_install.target()
        self.assertEqual(found, {"kind": "windows-portable", "app": Path("/data/Blanketa"), "all_users": False})
        self.assertIsNone(update_install.not_replaceable_reason())


class Linux(WithPatches):
    def test_folder_in_the_data_home_is_installable(self):
        with tempfile.TemporaryDirectory() as home:
            self.run_as(Path(home) / "blanketa/blanketa", env={"XDG_DATA_HOME": home})
            self.assertEqual(
                update_install.target(),
                {"kind": "linux-folder", "app": Path(home) / "blanketa", "all_users": False},
            )

    def test_default_data_home(self):
        self.run_as(Path.home() / ".local/share/blanketa/blanketa")
        with mock.patch.dict(os.environ):
            os.environ.pop("XDG_DATA_HOME", None)
            self.assertEqual(update_install.target()["kind"], "linux-folder")

    def test_deb_in_opt_is_not_installable(self):
        self.run_as("/opt/blanketa/blanketa", env={"XDG_DATA_HOME": "/home/x/.local/share"})
        self.assertIsNone(update_install.target())

    def test_unpacked_tar_folder_run_directly_is_not_installable(self):
        self.run_as("/home/x/Downloads/blanketa/blanketa", env={"XDG_DATA_HOME": "/home/x/.local/share"})
        self.assertIsNone(update_install.target())

    def test_unwritable_folder_is_not_installable(self):
        self.run_as("/home/x/.local/share/blanketa/blanketa", env={"XDG_DATA_HOME": "/home/x/.local/share"}, writable=False)
        self.assertIsNone(update_install.target())
