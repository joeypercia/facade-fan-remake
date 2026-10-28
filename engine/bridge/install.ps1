<#
Install the Façade Fan Remake bridge into your Façade folder.

  - renames the original nativeaniminterface.dll to nativeaniminterface_orig.dll
  - copies the proxy in as nativeaniminterface.dll
  - keeps a second copy of the original in %LOCALAPPDATA%\FacadeFanRemake\backup
Undo with uninstall.ps1. Asks for administrator rights (the game lives in Program Files).
#>
param([string]$Facade = "C:\Program Files (x86)\Facade")
$ErrorActionPreference = "Stop"

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Facade `"$Facade`""
    exit
}

$dir = Join-Path $Facade "util\sources\facade"
$live = Join-Path $dir "nativeaniminterface.dll"
$orig = Join-Path $dir "nativeaniminterface_orig.dll"
$proxy = Join-Path $PSScriptRoot "out\nativeaniminterface.dll"
$backup = Join-Path $env:LOCALAPPDATA "FacadeFanRemake\backup"
$log = Join-Path $env:LOCALAPPDATA "FacadeFanRemake\install.log"
New-Item -ItemType Directory -Force $backup | Out-Null
Start-Transcript -Path $log -Append | Out-Null
try {
    if (Get-Process animEngineStarter, java -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$Facade*" -or -not $_.Path }) {
        throw "Close Façade first."
    }
    if (-not (Test-Path $proxy)) { throw "Build the bridge first (build.bat): $proxy not found." }
    if (Test-Path $orig) {
        Write-Host "Already installed: updating the proxy only."
    } else {
        # The original is a 2005 build: make sure we're renaming that, not something else.
        $sig = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($live))
        if ($sig -notmatch "nativeaniminterface\.pdb") { throw "$live doesn't look like Façade's original bridge." }
        Copy-Item $live (Join-Path $backup "nativeaniminterface.dll") -Force
        Rename-Item $live "nativeaniminterface_orig.dll"
    }
    Copy-Item $proxy $live -Force
    Write-Host "Installed. Log of bridge calls: $env:LOCALAPPDATA\FacadeFanRemake\bridge.log"
} finally {
    Stop-Transcript | Out-Null
}
