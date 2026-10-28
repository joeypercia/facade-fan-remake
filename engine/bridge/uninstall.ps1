<#
Remove the Façade Fan Remake bridge and restore Façade's original nativeaniminterface.dll.
Asks for administrator rights.
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
$backup = Join-Path $env:LOCALAPPDATA "FacadeFanRemake\backup\nativeaniminterface.dll"
$log = Join-Path $env:LOCALAPPDATA "FacadeFanRemake\install.log"
Start-Transcript -Path $log -Append | Out-Null
try {
    if (Get-Process animEngineStarter, java -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$Facade*" -or -not $_.Path }) {
        throw "Close Façade first."
    }
    if (Test-Path $orig) {
        Remove-Item $live -Force -ErrorAction SilentlyContinue
        Rename-Item $orig "nativeaniminterface.dll"
        Write-Host "Restored the original bridge."
    } elseif (Test-Path $backup) {
        Copy-Item $backup $live -Force
        Write-Host "Restored the original bridge from the backup copy."
    } else {
        Write-Host "Nothing to undo: the bridge isn't installed."
    }
} finally {
    Stop-Transcript | Out-Null
}
