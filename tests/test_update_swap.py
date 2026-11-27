import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import ROOT

SWAP_SH = ROOT / "packaging/update/swap.sh"
SWAP_PS1 = ROOT / "packaging/update/swap.ps1"
INNO_SETUP = ROOT / "packaging/windows/blanketa.iss"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
IS_WINDOWS_WITH_POWERSHELL = sys.platform == "win32" and POWERSHELL is not None
IS_MAC = sys.platform == "darwin"
VERSION = "2.0.0"
SECONDS_FOR_A_SWAP = 30
SECONDS_TO_SEE_THE_SCRIPT_WAIT = 1


def waiting_process():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def ended_process_id():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def make_app(folder, program, content):
    executable = folder / program
    executable.parent.mkdir(parents=True)
    executable.write_text(content, encoding="utf-8")
    return folder


def read_only(folder):
    folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
    return folder


class Installation(unittest.TestCase):
    app_name = "Blanketa.app" if IS_MAC else "Blanketa"
    program = "Contents/MacOS/Blanketa" if IS_MAC else "Blanketa"
    staging_name = f".Blanketa-update-{VERSION}" if IS_MAC else f".blanketa-update-{VERSION}"
    script = SWAP_SH

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(self.remove_everything)
        self.apps = self.root / "apps"
        self.target = make_app(self.apps / self.app_name, self.program, "old")
        self.staging = self.apps / self.staging_name
        self.staged = make_app(self.staging / self.app_name, self.program, "new")
        self.updates = self.root / "config/updates"
        (self.updates / VERSION).mkdir(parents=True)
        (self.updates / VERSION / "download").write_text("archive", encoding="utf-8")
        (self.updates / "ready.json").write_text(json.dumps({"version": VERSION}), encoding="utf-8")
        (self.updates / "install.lock").write_text(json.dumps({"pid": 1}), encoding="utf-8")
        self.helper = self.updates / self.script.name
        shutil.copy(self.script, self.helper)
        self.record = self.root / "relaunched.txt"
        self.environment = {**os.environ, "BLANKETA_UPDATE_RELAUNCH_RECORD": str(self.record)}

    def remove_everything(self):
        for folder, _, _ in os.walk(self.root):
            os.chmod(folder, stat.S_IRWXU)
        shutil.rmtree(self.root)

    def installed(self):
        return (self.target / self.program).read_text(encoding="utf-8")

    def old_copy(self):
        return self.apps / f"{self.app_name}.old-{VERSION}"

    def failed_version(self):
        return json.loads((self.updates / "failed.json").read_text(encoding="utf-8"))["version"]

    def relaunches(self):
        if not self.record.exists():
            return []
        return [line.split("\t") for line in self.record.read_text(encoding="utf-8").splitlines()]


@unittest.skipIf(sys.platform == "win32", "swap.sh runs on macOS and Linux")
class SwapScript(Installation):
    def test_install_lock_is_removed_after_success_and_failure(self):
        self.assertEqual(self.swap(), 0)
        self.assertFalse((self.updates / "install.lock").exists())
        (self.updates / "install.lock").write_text("{}", encoding="utf-8")
        self.assertNotEqual(self.swap(staged=self.root / "chybi"), 0)
        self.assertFalse((self.updates / "install.lock").exists())

    def start(self, pid, relaunch=0, files=(), staged=None):
        arguments = [str(pid), str(staged or self.staged), str(self.target), str(self.updates), VERSION, str(relaunch),
                     *files]
        return subprocess.Popen(["/bin/sh", str(self.helper), *arguments], cwd=self.updates, env=self.environment,
                                stderr=subprocess.DEVNULL)

    def swap(self, **options):
        script = self.start(ended_process_id(), **options)
        return script.wait(timeout=SECONDS_FOR_A_SWAP)

    def test_script_is_valid_shell(self):
        subprocess.run(["/bin/sh", "-n", str(SWAP_SH)], check=True)

    def test_swap_replaces_after_the_process_exits(self):
        running = waiting_process()
        script = self.start(running.pid)
        time.sleep(SECONDS_TO_SEE_THE_SCRIPT_WAIT)
        self.assertIsNone(script.poll())
        self.assertEqual(self.installed(), "old")
        running.kill()
        running.wait()
        self.assertEqual(script.wait(timeout=SECONDS_FOR_A_SWAP), 0)
        self.assertEqual(self.installed(), "new")

    def test_success_removes_ready_and_old(self):
        self.assertEqual(self.swap(), 0)
        self.assertEqual(self.installed(), "new")
        self.assertFalse(self.old_copy().exists())
        self.assertFalse(self.staging.exists())
        self.assertFalse((self.updates / "ready.json").exists())
        self.assertFalse((self.updates / VERSION).exists())
        self.assertFalse((self.updates / "failed.json").exists())

    def test_failed_move_restores_the_old_app(self):
        read_only(self.apps)
        self.assertNotEqual(self.swap(), 0)
        self.assertEqual(self.installed(), "old")
        self.assertEqual(self.failed_version(), VERSION)
        self.assertFalse((self.updates / "ready.json").exists())

    def test_failure_after_the_old_app_moved_aside_restores_it(self):
        read_only(self.staging)
        self.assertNotEqual(self.swap(), 0)
        self.assertEqual(self.installed(), "old")
        self.assertFalse(self.old_copy().exists())
        self.assertEqual(self.failed_version(), VERSION)
        self.assertFalse((self.updates / "ready.json").exists())

    def test_incomplete_staged_app_changes_nothing(self):
        (self.staged / self.program).unlink()
        self.assertNotEqual(self.swap(), 0)
        self.assertEqual(self.installed(), "old")
        self.assertEqual(self.failed_version(), VERSION)

    def test_leftover_old_copy_is_replaced_when_the_app_is_complete(self):
        make_app(self.old_copy(), self.program, "older")
        self.assertEqual(self.swap(), 0)
        self.assertEqual(self.installed(), "new")
        self.assertFalse(self.old_copy().exists())

    def test_leftover_old_copy_is_kept_when_the_app_is_incomplete(self):
        make_app(self.old_copy(), self.program, "older")
        (self.target / self.program).unlink()
        self.assertNotEqual(self.swap(), 0)
        self.assertEqual((self.old_copy() / self.program).read_text(encoding="utf-8"), "older")
        self.assertEqual(self.failed_version(), VERSION)

    def test_relaunch_gets_the_files(self):
        files = [str(self.root / "a form.zfo"), str(self.root / "b.pdf")]
        self.assertEqual(self.swap(relaunch=1, files=files), 0)
        expected = ["open", "-a", str(self.target), *files] if IS_MAC else [str(self.target / "Blanketa"), *files]
        self.assertEqual(self.relaunches(), [expected])

    def test_relaunch_without_files_opens_the_app(self):
        self.assertEqual(self.swap(relaunch=1), 0)
        expected = ["open", str(self.target)] if IS_MAC else [str(self.target / "Blanketa")]
        self.assertEqual(self.relaunches(), [expected])

    def test_no_relaunch_when_not_asked(self):
        self.assertEqual(self.swap(), 0)
        self.assertEqual(self.relaunches(), [])

    def test_failed_swap_relaunches_the_old_app(self):
        read_only(self.staging)
        self.assertNotEqual(self.swap(relaunch=1), 0)
        self.assertEqual(len(self.relaunches()), 1)
        self.assertEqual(self.installed(), "old")

    def run_with(self, *arguments):
        script = subprocess.Popen(["/bin/sh", str(self.helper), *arguments], cwd=self.updates, env=self.environment,
                                  stderr=subprocess.DEVNULL)
        return script.wait(timeout=SECONDS_FOR_A_SWAP)

    def assert_untouched(self):
        self.assertEqual(self.installed(), "old")
        self.assertEqual((self.staged / self.program).read_text(encoding="utf-8"), "new")
        self.assertTrue((self.updates / "ready.json").exists())
        self.assertTrue((self.updates / VERSION).exists())
        self.assertFalse((self.updates / "failed.json").exists())

    def test_malformed_version_changes_nothing(self):
        for version in ('1"}', "..", ".", "1..2", "1.2", "", "1.2.3.4", "1.2.3\n4"):
            with self.subTest(version=version):
                code = self.run_with(str(ended_process_id()), str(self.staged), str(self.target), str(self.updates),
                                     version, "0")
                self.assertEqual(code, 2)
                self.assert_untouched()

    def test_relative_paths_change_nothing(self):
        relative = os.path.relpath(self.target, self.updates)
        arguments = [str(self.staged), str(self.target), str(self.updates)]
        for position in range(len(arguments)):
            with self.subTest(position=position):
                changed = list(arguments)
                changed[position] = relative if position == 1 else os.path.relpath(changed[position], self.updates)
                self.assertEqual(self.run_with(str(ended_process_id()), *changed, VERSION, "0"), 2)
                self.assert_untouched()

    def test_malformed_process_id_changes_nothing(self):
        for pid in ("0", "-1", "", "12a"):
            with self.subTest(pid=pid):
                code = self.run_with(pid, str(self.staged), str(self.target), str(self.updates), VERSION, "0")
                self.assertEqual(code, 2)
                self.assert_untouched()

    def test_relaunch_drops_files_that_are_not_absolute(self):
        kept = str(self.root / "b.pdf")
        self.assertEqual(self.swap(relaunch=1, files=["-x", "a.zfo", kept]), 0)
        expected = ["open", "-a", str(self.target), kept] if IS_MAC else [str(self.target / "Blanketa"), kept]
        self.assertEqual(self.relaunches(), [expected])

    def test_missing_app_is_restored_from_a_complete_leftover(self):
        shutil.rmtree(self.target)
        make_app(self.old_copy(), self.program, "older")
        self.assertEqual(self.swap(), 1)
        self.assertEqual(self.installed(), "older")
        self.assertFalse(self.old_copy().exists())
        self.assertEqual(self.failed_version(), VERSION)


    def test_shutdown_signals_during_the_swap_do_not_leave_the_app_missing(self):
        shims = self.root / "shims"
        shims.mkdir()
        (shims / "mv").write_text(f'#!/bin/sh\nkill -TERM "$PPID"\nkill -INT "$PPID"\nexec {shutil.which("mv")} "$@"\n',
                                  encoding="utf-8")
        (shims / "mv").chmod(stat.S_IRWXU)
        self.environment["PATH"] = f"{shims}{os.pathsep}{self.environment['PATH']}"
        self.assertEqual(self.swap(), 0)
        self.assertEqual(self.installed(), "new")
        self.assertFalse(self.old_copy().exists())


class WindowsScript(Installation):
    app_name = "Blanketa"
    program = "Blanketa.exe"
    staging_name = f".blanketa-update-{VERSION}"
    script = SWAP_PS1

    def start(self, pid, *options, kind="windows-portable", staged=None, target=None):
        arguments = ["-WaitPid", str(pid), "-Kind", kind, "-Staged", str(staged or self.staged),
                     "-Target", str(target or self.target), "-UpdatesDir", str(self.updates), "-Version", VERSION,
                     *options]
        return subprocess.Popen([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(self.helper), *arguments], cwd=self.updates, env=self.environment)

    def swap(self, *options, **keywords):
        script = self.start(ended_process_id(), *options, **keywords)
        return script.wait(timeout=SECONDS_FOR_A_SWAP)


@unittest.skipUnless(IS_WINDOWS_WITH_POWERSHELL, "swap.ps1 runs on Windows")
class PortableSwapOnWindows(WindowsScript):
    def test_install_lock_is_removed_after_success_and_failure(self):
        self.assertEqual(self.swap(), 0)
        self.assertFalse((self.updates / "install.lock").exists())
        (self.updates / "install.lock").write_text("{}", encoding="utf-8")
        self.assertNotEqual(self.swap(staged=self.root / "chybi"), 0)
        self.assertFalse((self.updates / "install.lock").exists())

    def test_swap_replaces_after_the_process_exits(self):
        running = waiting_process()
        script = self.start(running.pid)
        time.sleep(SECONDS_TO_SEE_THE_SCRIPT_WAIT)
        self.assertIsNone(script.poll())
        self.assertEqual(self.installed(), "old")
        running.kill()
        running.wait()
        self.assertEqual(script.wait(timeout=SECONDS_FOR_A_SWAP), 0)
        self.assertEqual(self.installed(), "new")

    def test_success_removes_ready_and_old(self):
        self.assertEqual(self.swap(), 0)
        self.assertEqual(self.installed(), "new")
        self.assertFalse(self.old_copy().exists())
        self.assertFalse(self.staging.exists())
        self.assertFalse((self.updates / "ready.json").exists())
        self.assertFalse((self.updates / VERSION).exists())

    def test_failed_move_restores_the_old_app(self):
        with open(self.target / self.program, "rb"):
            self.assertNotEqual(self.swap(), 0)
        self.assertEqual(self.installed(), "old")
        self.assertEqual(self.failed_version(), VERSION)
        self.assertFalse((self.updates / "ready.json").exists())

    def test_failure_after_the_old_app_moved_aside_restores_it(self):
        with open(self.staged / self.program, "rb"):
            self.assertNotEqual(self.swap(), 0)
        self.assertEqual(self.installed(), "old")
        self.assertFalse(self.old_copy().exists())
        self.assertEqual(self.failed_version(), VERSION)

    def test_failed_json_has_no_byte_order_mark(self):
        (self.staged / self.program).unlink()
        self.assertNotEqual(self.swap(), 0)
        self.assertFalse((self.updates / "failed.json").read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_relaunch_gets_the_files(self):
        files = [str(self.root / "a form.zfo"), str(self.root / "b.pdf")]
        self.assertEqual(self.swap("-Relaunch", *files), 0)
        self.assertEqual(self.relaunches(), [[str(self.target / "Blanketa.exe"), *files]])

    def test_no_relaunch_when_not_asked(self):
        self.assertEqual(self.swap(), 0)
        self.assertEqual(self.relaunches(), [])

    def test_relative_target_changes_nothing(self):
        self.assertEqual(self.swap(target=os.path.relpath(self.target, self.updates)), 2)
        self.assertEqual(self.installed(), "old")
        self.assertTrue((self.updates / "ready.json").exists())
        self.assertFalse((self.updates / "failed.json").exists())

    def test_relaunch_drops_files_that_are_not_absolute(self):
        kept = str(self.root / "b.pdf")
        self.assertEqual(self.swap("-Relaunch", "a.zfo", kept), 0)
        self.assertEqual(self.relaunches(), [[str(self.target / "Blanketa.exe"), kept]])

    def test_missing_app_is_restored_from_a_complete_leftover(self):
        shutil.rmtree(self.target)
        make_app(self.old_copy(), self.program, "older")
        self.assertEqual(self.swap(), 1)
        self.assertEqual(self.installed(), "older")
        self.assertFalse(self.old_copy().exists())
        self.assertEqual(self.failed_version(), VERSION)


@unittest.skipUnless(IS_WINDOWS_WITH_POWERSHELL, "swap.ps1 runs on Windows")
class SetupOnWindows(WindowsScript):
    def fake_setup(self, exit_code):
        setup = self.updates / VERSION / "Blanketa-2.0.0-setup.cmd"
        arguments = self.root / "setup-arguments.txt"
        setup.write_text(f'@echo %* > "{arguments}"\r\n@exit /b {exit_code}\r\n', encoding="ascii")
        return setup, arguments

    def install(self, exit_code, *options, sha256=None):
        setup, arguments = self.fake_setup(exit_code)
        digest = sha256 or hashlib.sha256(setup.read_bytes()).hexdigest()
        code = self.swap("-Sha256", digest, *options, kind="windows-setup", staged=setup)
        return code, arguments

    def test_setup_runs_silently_for_the_current_user(self):
        code, arguments = self.install(0)
        self.assertEqual(code, 0)
        recorded = arguments.read_text(encoding="ascii")
        for switch in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER",
                       f"/LOG=\"{self.updates / f'setup-{VERSION}.log'}\""):
            self.assertIn(switch, recorded)
        self.assertFalse((self.updates / "ready.json").exists())
        self.assertFalse((self.updates / VERSION).exists())

    def test_setup_for_all_users(self):
        code, arguments = self.install(0, "-AllUsers")
        self.assertEqual(code, 0)
        self.assertIn("/ALLUSERS", arguments.read_text(encoding="ascii"))
        self.assertNotIn("/CURRENTUSER", arguments.read_text(encoding="ascii"))

    def test_failed_setup_writes_failed(self):
        code, _ = self.install(5)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.failed_version(), VERSION)
        self.assertFalse((self.updates / "ready.json").exists())

    def test_changed_setup_is_not_run(self):
        code, arguments = self.install(0, sha256="0" * 64)
        self.assertNotEqual(code, 0)
        self.assertFalse(arguments.exists())
        self.assertEqual(self.failed_version(), VERSION)

    def test_relaunch_after_setup_starts_the_installed_app(self):
        code, _ = self.install(0, "-Relaunch", str(self.root / "b.pdf"))
        self.assertEqual(code, 0)
        self.assertEqual(self.relaunches(), [[str(self.target / "Blanketa.exe"), str(self.root / "b.pdf")]])


class InnoSetupForUpdates(unittest.TestCase):
    def test_setup_leaves_closing_blanketa_to_the_update_script(self):
        setup = INNO_SETUP.read_text(encoding="utf-8").splitlines()
        self.assertIn("CloseApplications=no", setup)
        self.assertIn(r"AppMutex=BlanketaRunning,Global\BlanketaRunning", setup)


class Bundling(unittest.TestCase):
    def test_scripts_are_bundled_on_every_platform(self):
        self.assertIn('"$APP/Contents/Resources/update/swap.sh"', (ROOT / "build.sh").read_text(encoding="utf-8"))
        desktop_build = (ROOT / "tools/build_desktop.py").read_text(encoding="utf-8")
        self.assertIn('data("packaging/update/swap.ps1", "update")', desktop_build)
        self.assertIn('data("packaging/update/swap.sh", "update")', desktop_build)


if __name__ == "__main__":
    unittest.main()
