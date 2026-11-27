[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory = $true)][int]$WaitPid,
    [Parameter(Mandatory = $true)][ValidateSet('windows-setup', 'windows-portable')][string]$Kind,
    [Parameter(Mandatory = $true)][string]$Staged,
    [Parameter(Mandatory = $true)][string]$Target,
    [Parameter(Mandatory = $true)][string]$UpdatesDir,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+$')][string]$Version,
    [string]$Sha256 = '',
    [switch]$AllUsers,
    [switch]$Relaunch,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Files = @()
)

$ErrorActionPreference = 'Stop'

$SecondsToWaitForExit = 120
$MillisecondsPerPoll = 250
$Program = 'Blanketa.exe'
$Old = "$Target.old-$Version"
$Utf8WithoutBom = New-Object System.Text.UTF8Encoding $false

function Test-AbsolutePath([string]$Path) {
    if ([string]::IsNullOrEmpty($Path) -or $Path.StartsWith('-')) { return $false }
    try { [System.IO.Path]::IsPathRooted($Path) } catch { $false }
}

if ($WaitPid -le 0) { exit 2 }
foreach ($Path in @($Staged, $Target, $UpdatesDir)) {
    if (-not (Test-AbsolutePath $Path)) { exit 2 }
}
$Files = @($Files | Where-Object { Test-AbsolutePath $_ })

function ConvertTo-QuotedArgument([string]$Argument) {
    '"' + ($Argument -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

function Start-Blanketa {
    if (-not $Relaunch) { return }
    $Executable = Join-Path $Target $Program
    if ($env:BLANKETA_UPDATE_RELAUNCH_RECORD) {
        $Line = (@($Executable) + $Files) -join "`t"
        [System.IO.File]::AppendAllText($env:BLANKETA_UPDATE_RELAUNCH_RECORD, "$Line`n", $Utf8WithoutBom)
        return
    }
    if ($Files.Count -gt 0) {
        Start-Process -FilePath $Executable -ArgumentList (($Files | ForEach-Object { ConvertTo-QuotedArgument $_ }) -join ' ')
    } else {
        Start-Process -FilePath $Executable
    }
}

function Write-Failure {
    try {
        [System.IO.File]::WriteAllText((Join-Path $UpdatesDir 'failed.json'), "{`"version`": `"$Version`"}`n", $Utf8WithoutBom)
    } catch { }
    Remove-Item -LiteralPath (Join-Path $UpdatesDir 'ready.json') -Force -ErrorAction SilentlyContinue
}

function Exit-Failed {
    Write-Failure
    Start-Blanketa
    exit 1
}

function Exit-Succeeded {
    Remove-Item -LiteralPath (Join-Path $UpdatesDir $Version) -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $UpdatesDir 'ready.json') -Force -ErrorAction SilentlyContinue
    Start-Blanketa
    exit 0
}

function Wait-ForExit {
    $Deadline = (Get-Date).AddSeconds($SecondsToWaitForExit)
    while (Get-Process -Id $WaitPid -ErrorAction SilentlyContinue) {
        if ((Get-Date) -gt $Deadline) {
            Write-Failure
            exit 1
        }
        Start-Sleep -Milliseconds $MillisecondsPerPoll
    }
}

function Test-App([string]$Folder) {
    Test-Path -LiteralPath (Join-Path $Folder $Program) -PathType Leaf
}

function Restore-OldApp {
    if ((Test-Path -LiteralPath $Target) -and -not (Test-Path -LiteralPath $Staged)) {
        try { [System.IO.Directory]::Move($Target, $Staged) } catch { Remove-Item -LiteralPath $Target -Recurse -Force -ErrorAction SilentlyContinue }
    }
    if (-not (Test-Path -LiteralPath $Target)) {
        [System.IO.Directory]::Move($Old, $Target)
    }
}

function Install-Portable {
    if (Test-Path -LiteralPath $Old) {
        if (-not (Test-Path -LiteralPath $Target) -and (Test-App $Old)) {
            try { [System.IO.Directory]::Move($Old, $Target) } catch { }
            Exit-Failed
        }
        if (-not (Test-App $Target)) { Exit-Failed }
        try { Remove-Item -LiteralPath $Old -Recurse -Force } catch { Exit-Failed }
    }
    if (-not (Test-App $Staged)) { Exit-Failed }
    try { [System.IO.Directory]::Move($Target, $Old) } catch { Exit-Failed }
    try {
        [System.IO.Directory]::Move($Staged, $Target)
        if (-not (Test-App $Target)) { throw 'The moved app has no program' }
    } catch {
        try { Restore-OldApp } catch { }
        Exit-Failed
    }
    Remove-Item -LiteralPath $Old -Recurse -Force -ErrorAction SilentlyContinue
    try { [System.IO.Directory]::Delete((Split-Path -Parent $Staged)) } catch { }
    Exit-Succeeded
}

function Get-Sha256([System.IO.Stream]$Stream) {
    $Hasher = [System.Security.Cryptography.SHA256]::Create()
    try { -join ($Hasher.ComputeHash($Stream) | ForEach-Object { $_.ToString('x2') }) } finally { $Hasher.Dispose() }
}

function Install-Setup {
    try {
        $Held = New-Object System.IO.FileStream($Staged, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    } catch { Exit-Failed }
    try { $Digest = Get-Sha256 $Held } catch { $Held.Dispose(); Exit-Failed }
    if ($Digest -ne $Sha256) { $Held.Dispose(); Exit-Failed }
    $Scope = if ($AllUsers) { '/ALLUSERS' } else { '/CURRENTUSER' }
    $Log = Join-Path $UpdatesDir "setup-$Version.log"
    $Start = New-Object System.Diagnostics.ProcessStartInfo
    $Start.FileName = $Staged
    $Start.Arguments = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART $Scope /LOG=$(ConvertTo-QuotedArgument $Log)"
    $Start.UseShellExecute = $false
    try {
        try { $Setup = [System.Diagnostics.Process]::Start($Start) } finally { $Held.Dispose() }
        $Setup.WaitForExit()
        $ExitCode = $Setup.ExitCode
    } catch { Exit-Failed }
    if ($ExitCode -ne 0) { Exit-Failed }
    Exit-Succeeded
}

Wait-ForExit
if ($Kind -eq 'windows-setup') { Install-Setup } else { Install-Portable }
