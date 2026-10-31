"""Builds Blanketa for Windows or Linux on that system (PyInstaller cannot cross-compile) into dist/:

    Windows: dist/Blanketa/ (Blanketa.exe), Blanketa-<version>-windows-x64.zip, and with Inno Setup installed
             Blanketa-<version>-setup.exe (installer that opens .zfo files with Blanketa)
    Linux:   dist/Blanketa/, blanketa_<version>_<arch>.deb (Ubuntu, Debian, Mint …) and blanketa-<version>-linux-<arch>.tar.gz

    python tools/build_desktop.py
The macOS app is built by build.sh (Swift window + the same server).
"""
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
EDITOR = "vendor/zfo-editor-src/zfo-editor-0.3.2"
IS_WINDOWS = sys.platform == "win32"
DIST = ROOT / "dist"


def data(source, target):
    return ["--add-data", f"{ROOT / source}{os.pathsep}{target}"]


def pyinstaller():
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN", "--onedir",
            "--name", "Blanketa", "--distpath", str(DIST), "--workpath", str(ROOT / "build/desktop"),
            "--specpath", str(ROOT / "build"), "--icon", str(ROOT / "assets/blanketa.ico"),
            *data(f"{EDITOR}/xslt", f"{EDITOR}/xslt"), *data(f"{EDITOR}/scripts", f"{EDITOR}/scripts"),
            *data(f"{EDITOR}/styles", f"{EDITOR}/styles"), *data("ui", "ui"), *data("vendor/pdfjs", "vendor/pdfjs"),
            *data("assets/blanketa.png", "assets"),
            "--collect-all", "pyhanko", "--collect-all", "pyhanko_certvalidator", "--collect-all", "uharfbuzz",
            "--collect-submodules", "fontTools", "--collect-data", "tzdata", "--collect-data", "certifi", "--collect-submodules", "zfoengine",
            "--hidden-import", "app", "--hidden-import", "lxml._elementpath", "--collect-all", "webview"]
    if IS_WINDOWS:
        args += ["--windowed", *data("winsign.ps1", ".")]
    else:
        args += ["--hidden-import", "qtpy", "--collect-submodules", "PyQt6.QtWebEngineWidgets"]
    subprocess.run([*args, str(ROOT / "desktop.py")], check=True, cwd=ROOT)
    # GPL/LGPL: licence texts and the source of the modified library travel with the app.
    licence = DIST / "Blanketa/Licence"
    shutil.rmtree(licence, ignore_errors=True)
    (licence / "zfoengine").mkdir(parents=True)
    shutil.copy(ROOT / "LICENSE", licence)
    shutil.copy(ROOT / "THIRD-PARTY-NOTICES.md", licence)
    for f in [*(ROOT / "zfoengine").glob("*.py"), ROOT / "zfoengine/LICENSE"]:
        shutil.copy(f, licence / "zfoengine")
    shutil.copy(ROOT / "vendor/pdfjs/LICENSE", licence / "pdfjs-LICENSE")


def package_windows():
    archive = shutil.make_archive(str(DIST / f"Blanketa-{VERSION}-windows-x64"), "zip", DIST, "Blanketa")
    print("built", archive)
    iscc = shutil.which("iscc") or next((str(p) for p in (Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6/ISCC.exe",)
                                         if p.exists()), None)
    if iscc:
        subprocess.run([iscc, f"/DVersion={VERSION}", f"/DSource={DIST / 'Blanketa'}", f"/O{DIST}",
                        str(ROOT / "packaging/windows/blanketa.iss")], check=True)
    else:
        print("Inno Setup (iscc) not found: installer skipped")


def package_linux():
    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
    with tarfile.open(DIST / f"blanketa-{VERSION}-linux-{arch}.tar.gz", "w:gz") as tar:
        tar.add(DIST / "Blanketa", arcname="Blanketa")
        tar.add(ROOT / "packaging/linux/install.sh", arcname="Blanketa/install.sh")
    # .deb: the app in /opt, a launcher, a menu entry, the icon and the ZFO file type.
    root = ROOT / "build/deb"
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(DIST / "Blanketa", root / "opt/blanketa")
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/bin/blanketa").symlink_to("/opt/blanketa/Blanketa")
    for source, target in (("packaging/linux/blanketa.desktop", "usr/share/applications/blanketa.desktop"),
                           ("packaging/linux/blanketa-mime.xml", "usr/share/mime/packages/blanketa.xml"),
                           ("assets/blanketa.png", "usr/share/icons/hicolor/512x512/apps/blanketa.png")):
        (root / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / source, root / target)
    (root / "DEBIAN").mkdir()
    size = sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) // 1024
    control = (ROOT / "packaging/linux/control").read_text(encoding="utf-8").replace("@VERSION@", VERSION)
    (root / "DEBIAN/control").write_text(control.replace("@ARCH@", arch).replace("@SIZE@", str(size)), encoding="utf-8")
    for script in ("postinst", "postrm"):
        shutil.copy(ROOT / f"packaging/linux/{script}", root / f"DEBIAN/{script}")
        (root / f"DEBIAN/{script}").chmod(0o755)
    deb = DIST / f"blanketa_{VERSION}_{arch}.deb"
    level = os.environ.get("DEB_COMPRESSION_LEVEL")
    subprocess.run(["dpkg-deb", "--root-owner-group", *([f"-z{level}"] if level else []), "--build", str(root), str(deb)], check=True)
    print("built", deb)


if __name__ == "__main__":
    pyinstaller()
    package_windows() if IS_WINDOWS else package_linux()
