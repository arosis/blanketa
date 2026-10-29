; Inno Setup installer for Blanketa on Windows (built by tools/build_desktop.py):
;   iscc /DVersion=0.1.0 /DSource=dist\Blanketa /Odist packaging\windows\blanketa.iss
; Installs for the current user without admin rights (or for everyone if chosen), adds Blanketa to
; "Otevřít v programu" for .zfo and .pdf and, as an optional task, makes it the default for .zfo files.

#ifndef Version
  #define Version "0.0.0"
#endif

[Setup]
AppId={{6C1F3E0A-9B7D-4E2B-8F51-B1A7E4C2D903}
AppName=Blanketa
AppVersion={#Version}
AppPublisher=Richard Winkler Bejr
AppPublisherURL=https://github.com/arosis/blanketa
DefaultDirName={autopf}\Blanketa
DefaultGroupName=Blanketa
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes
OutputBaseFilename=Blanketa-{#Version}-setup
SetupIconFile=..\..\assets\blanketa.ico
UninstallDisplayIcon={app}\Blanketa.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "czech"; MessagesFile: "compiler:Languages\Czech.isl"

[Tasks]
Name: "zfodefault"; Description: "Otevírat soubory .zfo v Blanketě (místo Form Filleru)"
Name: "desktopicon"; Description: "Zástupce na ploše"; Flags: unchecked

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Blanketa"; Filename: "{app}\Blanketa.exe"
Name: "{autodesktop}\Blanketa"; Filename: "{app}\Blanketa.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\Blanketa.zfo"; ValueType: string; ValueName: ""; ValueData: "Formulář 602XML (ZFO)"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Blanketa.zfo\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\Blanketa.exe,0"
Root: HKA; Subkey: "Software\Classes\Blanketa.zfo\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\Blanketa.exe"" ""%1"""
Root: HKA; Subkey: "Software\Classes\.zfo\OpenWithProgids"; ValueType: string; ValueName: "Blanketa.zfo"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.zfo"; ValueType: string; ValueName: ""; ValueData: "Blanketa.zfo"; Tasks: zfodefault; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Blanketa.pdf"; ValueType: string; ValueName: ""; ValueData: "Dokument PDF"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Blanketa.pdf\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\Blanketa.exe"" ""%1"""
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "Blanketa.pdf"; ValueData: ""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\Blanketa.exe"; Description: "Spustit Blanketu"; Flags: nowait postinstall skipifsilent

[Code]
// Blanketa shows its pages in Microsoft Edge WebView2. Windows 10/11 have it; older Windows versions
// may not, and then the user is pointed to Microsoft's installer.
function WebView2Installed: Boolean;
var Version: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) or
    RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version);
  Result := Result and (Version <> '') and (Version <> '0.0.0.0');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var ErrorCode: Integer;
begin
  if (CurStep = ssPostInstall) and not WebView2Installed then
    if MsgBox('Blanketa potřebuje Microsoft Edge WebView2 Runtime, který na tomto počítači chybí. Otevřít stránku ke stažení?',
              mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2/', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;
