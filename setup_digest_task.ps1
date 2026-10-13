<#
.SYNOPSIS
    Manages the Windows Task Scheduler task for the Local Mail Agent Daily Digest.

.DESCRIPTION
    Configures, runs, inspects, or unregisters a daily scheduled task that runs
    the deterministic Daily Digest using the project virtual environment.

.PARAMETER Action
    The action to perform: 'install' (default), 'status', 'run', or 'uninstall'.

.PARAMETER Time
    The daily scheduled time in 24-hour HH:MM format (default: '08:00').

.PARAMETER TaskName
    The name of the scheduled task (default: 'LocalMailAgent-DailyDigest').

.EXAMPLE
    .\setup_digest_task.ps1 -Action install -Time "08:00"
    .\setup_digest_task.ps1 -Action status
    .\setup_digest_task.ps1 -Action run
    .\setup_digest_task.ps1 -Action uninstall
#>

param(
    [ValidateSet("install", "status", "run", "uninstall")]
    [string]$Action = "install",

    [ValidatePattern("^([01]\d|2[0-3]):([0-5]\d)$")]
    [string]$Time = "08:00",

    [string]$TaskName = "LocalMailAgent-DailyDigest"
)

$ErrorActionPreference = "Stop"

# Resolve absolute paths
$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) {
    $ProjectRoot = (Get-Location).Path
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    # Fallback to system Python if .venv not present
    $VenvPython = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
}

if (-not (Test-Path $VenvPython)) {
    Write-Error "Could not locate Python executable in $VenvPython or system PATH."
    exit 1
}

$ScriptPath = Join-Path $ProjectRoot "run_digest_job.py"
if (-not (Test-Path $ScriptPath)) {
    Write-Error "Could not locate run_digest_job.py in $ScriptPath."
    exit 1
}

Write-Host "=== Local Mail Agent: Daily Digest Task Scheduler ===" -ForegroundColor Cyan
Write-Host "Task Name:        $TaskName"
Write-Host "Project Root:     $ProjectRoot"
Write-Host "Python Path:      $VenvPython"
Write-Host "Job Script:       $ScriptPath"

switch ($Action) {
    "install" {
        Write-Host "`nRegistering scheduled task to run daily at $Time..." -ForegroundColor Yellow

        $taskAction = New-ScheduledTaskAction `
            -Execute $VenvPython `
            -Argument "`"$ScriptPath`"" `
            -WorkingDirectory $ProjectRoot

        $taskTrigger = New-ScheduledTaskTrigger `
            -Daily `
            -At $Time

        $taskSettings = New-ScheduledTaskSettingsSet `
            -StartWhenAvailable `
            -MultipleInstances IgnoreNew `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Hours 1)

        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $taskAction `
            -Trigger $taskTrigger `
            -Settings $taskSettings `
            -Description "Local Mail Agent Daily Digest Job" `
            -Force | Out-Null

        Write-Host "[SUCCESS] Task '$TaskName' registered successfully." -ForegroundColor Green
        Write-Host "Scheduled to run daily at: $Time"
        Write-Host "Catch-up enabled: Will run as soon as possible after startup if missed while laptop was off."
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
        Write-Host "`nTriggering task '$TaskName' manually..." -ForegroundColor Yellow
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
