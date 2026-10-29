#!/bin/bash
# Builds "Blanketa.app" into build/: Swift shell + bundled Python server + macsign.
set -euo pipefail
cd "$(dirname "$0")"
V=vendor/zfo-editor-src/zfo-editor-0.3.2
APP="build/Blanketa.app"

.venv/bin/pyinstaller --noconfirm --clean --log-level WARN --onedir --name zfo-server \
  --distpath build/dist --workpath build/work --specpath build \
  --add-data "$PWD/$V/xslt:$V/xslt" --add-data "$PWD/$V/scripts:$V/scripts" --add-data "$PWD/$V/styles:$V/styles" \
  --add-data "$PWD/ui:ui" --add-data "$PWD/vendor/pdfjs:vendor/pdfjs" \
  --collect-all pyhanko --collect-all pyhanko_certvalidator --collect-all uharfbuzz --collect-submodules fontTools --collect-data tzdata \
  --collect-submodules zfoengine --hidden-import lxml._elementpath app.py

swiftc -O -suppress-warnings macsign/macsign.swift -o macsign/macsign
swiftc -O -suppress-warnings macapp/main.swift -o build/blanketa

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp macapp/Info.plist "$APP/Contents/Info.plist"
cp build/blanketa "$APP/Contents/MacOS/Blanketa"
cp macsign/macsign "$APP/Contents/Resources/macsign"
cp assets/blanketa.icns "$APP/Contents/Resources/blanketa.icns"
cp -R build/dist/zfo-server "$APP/Contents/Resources/zfo-server"
# GPL/LGPL: licence texts and the source of the modified library travel with the app.
L="$APP/Contents/Resources/Licence"
mkdir -p "$L/zfoengine"
cp LICENSE THIRD-PARTY-NOTICES.md "$L/"
cp zfoengine/*.py zfoengine/LICENSE "$L/zfoengine/"
cp vendor/pdfjs/LICENSE "$L/pdfjs-LICENSE"
codesign --force --deep --sign - "$APP"
echo "built: $APP"

# Download packages: a zip (keeps the app's signature and attributes) and a disk image.
VERSION=$(cat VERSION)
mkdir -p dist
rm -f "dist/Blanketa-$VERSION-macos.zip" "dist/Blanketa-$VERSION-macos.dmg"
ditto -c -k --keepParent "$APP" "dist/Blanketa-$VERSION-macos.zip"
hdiutil create -quiet -volname Blanketa -srcfolder "$APP" -ov -format UDZO "dist/Blanketa-$VERSION-macos.dmg"
echo "packaged: dist/Blanketa-$VERSION-macos.zip, dist/Blanketa-$VERSION-macos.dmg"
