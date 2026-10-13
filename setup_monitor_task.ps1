<#
.SYNOPSIS
    Manages the Windows Task Scheduler task for the Local Mail Agent Background Monitor Service.

.DESCRIPTION
    Configures, inspects, runs, or unregisters a scheduled task that launches
    the independent background email monitor service at user logon.

.PARAMETER Action
    The action to perform: 'install' (default), 'status', 'run', or 'uninstall'.

.PARAMETER TaskName
    The name of the scheduled task (default: 'LocalMailAgent-BackgroundMonitor').

.EXAMPLE
    .\setup_monitor_task.ps1 -Action install
    .\setup_monitor_task.ps1 -Action status
    .\setup_monitor_task.ps1 -Action run
    .\setup_monitor_task.ps1 -Action uninstall
#>

param(
    [ValidateSet("install", "status", "run", "uninstall")]
    [string]$Action = "install",

    [string]$TaskName = "LocalMailAgent-BackgroundMonitor"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) {
    $ProjectRoot = (Get-Location).Path
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    $VenvPython = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
}

if (-not (Test-Path $VenvPython)) {
    Write-Error "Could not locate Python executable in $VenvPython or system PATH."
    exit 1
}

$ScriptPath = Join-Path $ProjectRoot "monitor_service.py"
if (-not (Test-Path $ScriptPath)) {
    Write-Error "Could not locate monitor_service.py in $ScriptPath."
    exit 1
}

# Check for Administrator elevation for install/uninstall actions
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (($Action -eq "install" -or $Action -eq "uninstall") -and -not $isAdmin) {
    Write-Host "Elevated Administrator privileges are required to configure Windows Task Scheduler." -ForegroundColor Yellow
    Write-Host "Requesting Administrator elevation (UAC prompt)..." -ForegroundColor Cyan
    try {
        $proc = Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Action $Action -TaskName `"$TaskName`"" -PassThru -Wait
        exit $proc.ExitCode
    } catch {
        Write-Error "Could not launch elevated prompt. Please re-run PowerShell as Administrator (Right click -> Run as Administrator)."
        exit 1
    }
}

Write-Host "=== Local Mail Agent: Background Monitor Task Scheduler ===" -ForegroundColor Cyan
Write-Host "Task Name:        $TaskName"
Write-Host "Project Root:     $ProjectRoot"
Write-Host "Python Path:      $VenvPython"
Write-Host "Job Script:       $ScriptPath"

switch ($Action) {
    "install" {
        Write-Host "`nRegistering scheduled task to start at User Logon..." -ForegroundColor Yellow

        $taskAction = New-ScheduledTaskAction `
            -Execute $VenvPython `
            -Argument "`"$ScriptPath`"" `
            -WorkingDirectory $ProjectRoot

        $taskTrigger = New-ScheduledTaskTrigger -AtLogOn

        $taskSettings = New-ScheduledTaskSettingsSet `
            -StartWhenAvailable `
            -MultipleInstances IgnoreNew `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 5)

        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $taskAction `
            -Trigger $taskTrigger `
            -Settings $taskSettings `
            -Description "Local Mail Agent Continuous Background Monitoring Service" `
            -Force | Out-Null

        Write-Host "[SUCCESS] Task '$TaskName' registered successfully." -ForegroundColor Green
        Write-Host "Trigger: At User Logon"
        Write-Host "Catch-up enabled: Starts automatically when user logs in and operates in background."
    }

    "status" {
        Write-Host "`nQuerying scheduled task status..." -ForegroundColor Yellow
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            $info = Get-ScheduledTaskInfo -TaskName $TaskName
            [PSCustomObject]@{
                TaskName    = $task.TaskName
                State       = $task.State
                LastRunTime = $info.LastRunTime
                LastResult  = $info.LastTaskResult
                NextRunTime = $info.NextRunTime
            } | Format-List
        } else {
            Write-Host "Task '$TaskName' is not registered." -ForegroundColor DarkYellow
        }
    }

    "run" {
        Write-Host "`nTriggering task '$TaskName'..." -ForegroundColor Yellow
        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Seconds 2
        $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
        Write-Host "[SUCCESS] Task triggered. State: $($info.LastTaskResult)" -ForegroundColor Green
    }

    "uninstall" {
        Write-Host "`nUnregistering task '$TaskName'..." -ForegroundColor Yellow
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "[SUCCESS] Task '$TaskName' unregistered." -ForegroundColor Green
    }
}
