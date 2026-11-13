# Tries attachments in the built Windows app the way a user does: with the mouse, through the system's file dialogs.
#   powershell -File tools\try-attachments-on-windows.ps1 -App dist\Blanketa\Blanketa.exe -Form samples\skripty\prilohy.zfo -Results dist
# Captions are matched by their ASCII parts: Windows PowerShell 5.1 reads a script without a byte order mark as ANSI.
param([Parameter(Mandatory)][string]$App, [Parameter(Mandatory)][string]$Form, [Parameter(Mandatory)][string]$Results)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms, System.Drawing
Add-Type -Namespace Blanketa -Name Mouse -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
[DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
'@
$LEFT_DOWN, $LEFT_UP = 0x0002, 0x0004
$SYSTEM_DIALOG_CLASS = '#32770'
$FILE_NAME_FIELDS_OF_SYSTEM_DIALOGS = '1148', '1001'
$CONFIRMING_BUTTON_OF_SYSTEM_DIALOGS = '1'
$ATTACHMENT_NAME = 'priloha.pdf'
$Automation = [System.Windows.Automation.AutomationElement]
$Tree = [System.Windows.Automation.TreeScope]
$Everything = [System.Windows.Automation.Condition]::TrueCondition

function Save-Screenshot($name) {
  $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
  $shot = New-Object System.Drawing.Bitmap $screen.Width, $screen.Height
  [System.Drawing.Graphics]::FromImage($shot).CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)
  $shot.Save((Join-Path $Results "windows-attachments-$name.png"))
}

function Get-Described($element) {
  "$($element.Current.ControlType.ProgrammaticName) '$($element.Current.Name)' id=$($element.Current.AutomationId) class=$($element.Current.ClassName)"
}

function Find-Inside($root, [scriptblock]$fits) {
  foreach ($element in $root.FindAll($Tree::Descendants, $Everything)) {
    try { if (& $fits $element) { return $element } } catch [System.Windows.Automation.ElementNotAvailableException] { }
  }
}

function Wait-Until($what, [scriptblock]$look, $seconds = 60) {
  foreach ($attempt in 1..$seconds) {
    $found = & $look
    if ($found) { return $found }
    Start-Sleep 1
  }
  Save-Screenshot 'failed'
  Write-Host "On the screen while waiting for ${what}:"
  foreach ($window in $Automation::RootElement.FindAll($Tree::Children, $Everything)) {
    Write-Host "  $(Get-Described $window)"
    if ($window.Current.ProcessId -eq $script:Blanketa.Id -or $window.Current.ClassName -eq $SYSTEM_DIALOG_CLASS) {
      foreach ($element in $window.FindAll($Tree::Descendants, $Everything)) { Write-Host "      $(Get-Described $element)" }
    }
  }
  throw "did not see $what within $seconds s"
}

function Click-On($element) {
  $box = $element.Current.BoundingRectangle
  [Blanketa.Mouse]::SetCursorPos([int]($box.X + $box.Width / 2), [int]($box.Y + $box.Height / 2)) | Out-Null
  Start-Sleep -Milliseconds 300
  [Blanketa.Mouse]::mouse_event($LEFT_DOWN, 0, 0, 0, [UIntPtr]::Zero)
  [Blanketa.Mouse]::mouse_event($LEFT_UP, 0, 0, 0, [UIntPtr]::Zero)
}

function Get-FormWindow {
  foreach ($window in $Automation::RootElement.FindAll($Tree::Children, $Everything)) {
    if ($window.Current.ProcessId -eq $script:Blanketa.Id -and $window.Current.Name) { return $window }
  }
}

function Find-OnForm($caption) {
  Find-Inside (Get-FormWindow) { param($element) $element.Current.Name -like $caption -and -not $element.Current.IsOffscreen }
}

function Find-SystemDialog {
  $ofTheSystem = { param($element) $element.Current.ClassName -eq $SYSTEM_DIALOG_CLASS }
  foreach ($window in $Automation::RootElement.FindAll($Tree::Children, $Everything)) {
    if (& $ofTheSystem $window) { return $window }
    $inside = Find-Inside $window $ofTheSystem
    if ($inside) { return $inside }
  }
}

function Confirm-SystemDialogWith($path) {
  $dialog = Wait-Until 'a file dialog of the system' { Find-SystemDialog }
  $field = Wait-Until 'the file name field of the dialog' {
    Find-Inside $dialog { param($element)
      $element.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit -and $FILE_NAME_FIELDS_OF_SYSTEM_DIALOGS -contains $element.Current.AutomationId }
  } 20
  $field.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue($path)
  $confirm = Find-Inside $dialog { param($element)
    $element.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $element.Current.AutomationId -eq $CONFIRMING_BUTTON_OF_SYSTEM_DIALOGS }
  $confirm.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
  Wait-Until 'the file dialog to close' { -not (Find-SystemDialog) } 20 | Out-Null
}

New-Item -ItemType Directory -Force $Results | Out-Null
$Results = (Resolve-Path $Results).Path
$chosen = Join-Path $Results $ATTACHMENT_NAME
$saved = Join-Path $Results 'ulozena-priloha.pdf'
[System.IO.File]::WriteAllText($chosen, "%PDF-1.4`n% made up for trying attachments`n%%EOF`n")
Remove-Item $saved -ErrorAction SilentlyContinue
Get-Process Blanketa -ErrorAction SilentlyContinue | Stop-Process -Force

$script:Blanketa = Start-Process $App -PassThru -ArgumentList "`"$((Resolve-Path $Form).Path)`""
try {
  $window = Wait-Until 'the window of the app' { Get-FormWindow } 120
  [Blanketa.Mouse]::SetForegroundWindow([IntPtr]$window.Current.NativeWindowHandle) | Out-Null

  Click-On (Wait-Until 'the button that adds attachments' { Find-OnForm 'Nahrajte ze*' } 120)
  Confirm-SystemDialogWith $chosen
  $attachment = Wait-Until 'the name of the attachment on the form' { Find-OnForm $ATTACHMENT_NAME }
  Save-Screenshot 'added'

  Click-On $attachment
  Confirm-SystemDialogWith $saved
  Wait-Until 'the saved attachment' { Test-Path $saved } 20 | Out-Null
  if ((Get-FileHash $saved).Hash -ne (Get-FileHash $chosen).Hash) { throw 'the saved attachment differs from the chosen file' }

  Click-On (Wait-Until 'the button that removes the attachment' { Find-OnForm 'Odebrat*' })
  Wait-Until 'the attachment to leave the form' { -not (Find-OnForm $ATTACHMENT_NAME) } 30 | Out-Null
  Save-Screenshot 'removed'
  Write-Host 'attachments ok: added, saved identical, removed'
} finally {
  Get-Process Blanketa -ErrorAction SilentlyContinue | Stop-Process -Force
}
