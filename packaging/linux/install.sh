#!/bin/sh
# Installs Blanketa from this archive for the current user (no root): ~/.local/share/blanketa,
# a launcher in ~/.local/bin, a menu entry and the ZFO file type.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
rm -rf "$DATA/blanketa" && cp -R "$HERE" "$DATA/blanketa"
mkdir -p "$HOME/.local/bin" "$DATA/applications" "$DATA/mime/packages" "$DATA/icons/hicolor/512x512/apps"
ln -sf "$DATA/blanketa/Blanketa" "$HOME/.local/bin/blanketa"
cat > "$DATA/applications/blanketa.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Blanketa
Comment=Vyplňování a podepisování formulářů ZFO (602XML) a PDF
Exec=$DATA/blanketa/Blanketa %F
Icon=blanketa
Terminal=false
Categories=Office;Viewer;
MimeType=application/vnd.software602.filler.form-xml-zip;application/pdf;
DESKTOP
cp "$HERE/_internal/assets/blanketa.png" "$DATA/icons/hicolor/512x512/apps/blanketa.png"
cat > "$DATA/mime/packages/blanketa.xml" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/vnd.software602.filler.form-xml-zip">
    <comment>Formulář 602XML (ZFO)</comment>
    <glob pattern="*.zfo"/>
  </mime-type>
</mime-info>
XML
command -v update-mime-database >/dev/null && update-mime-database "$DATA/mime" || true
command -v update-desktop-database >/dev/null && update-desktop-database -q "$DATA/applications" || true
echo "Blanketa je nainstalovaná. Spustíte ji z nabídky aplikací nebo příkazem: blanketa"
