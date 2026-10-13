# Creates Windows Desktop and Start Menu shortcuts for Local Mail Agent

$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) {
    $ProjectRoot = (Get-Location).Path
}

$VenvPythonw = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $VenvPythonw)) {
    $VenvPythonw = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
}

$WshShell = New-Object -ComObject WScript.Shell

# 1. Create Desktop Shortcut
$DesktopPath = [Environment]::GetFolderPath('Desktop')
$DesktopShortcut = $WshShell.CreateShortcut((Join-Path $DesktopPath "Mail Agent.lnk"))
$DesktopShortcut.TargetPath = $VenvPythonw
$DesktopShortcut.Arguments = "main.py desktop"
$DesktopShortcut.WorkingDirectory = $ProjectRoot
$DesktopShortcut.Description = "Local Mail Agent AI Smart Inbox"
# Windows Mail icon from shell32.dll
$DesktopShortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,156"
$DesktopShortcut.Save()

# 2. Create Start Menu Shortcut
$StartMenuPath = [Environment]::GetFolderPath('Programs')
$StartMenuShortcut = $WshShell.CreateShortcut((Join-Path $StartMenuPath "Mail Agent.lnk"))
$StartMenuShortcut.TargetPath = $VenvPythonw
$StartMenuShortcut.Arguments = "main.py desktop"
$StartMenuShortcut.WorkingDirectory = $ProjectRoot
$StartMenuShortcut.Description = "Local Mail Agent AI Smart Inbox"
$StartMenuShortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,156"
$StartMenuShortcut.Save()

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  Mail Agent Desktop Shortcuts Created Successfully!" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "1. Desktop Icon   : $DesktopPath\Mail Agent.lnk"
Write-Host "2. Start Menu Icon: $StartMenuPath\Mail Agent.lnk"
Write-Host "`nYou can now launch Mail Agent like any native Windows app:"
Write-Host "- Double-click the 'Mail Agent' icon on your Desktop."
Write-Host "- Or press the Windows key, type 'Mail Agent', and hit Enter."
Write-Host "- Launches silently with no terminal window required!"
Write-Host "==========================================================" -ForegroundColor Cyan
