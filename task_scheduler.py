"""Windows Task Scheduler configuration and command builders for Daily Digest."""

from dataclasses import dataclass
from pathlib import Path
import re
import sys

DEFAULT_TASK_NAME = "LocalMailAgent-DailyDigest"
DEFAULT_SCHEDULE_TIME = "08:00"
TIME_REGEX = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class TaskConfig:
    task_name: str
    schedule_time: str
    python_exe: Path
    script_path: Path
    working_dir: Path

    def __post_init__(self) -> None:
        if not TIME_REGEX.match(self.schedule_time):
            raise ValueError(
                f"Invalid schedule time {self.schedule_time!r}. Must be 24-hour format HH:MM (e.g., '08:00', '14:30')."
            )
        if not self.task_name.strip():
            raise ValueError("task_name must not be empty.")


def get_default_paths(project_dir: Path | None = None) -> tuple[Path, Path, Path]:
    """Resolve project directory, virtual environment Python executable, and job script."""
    root = (project_dir or Path(__file__).resolve().parent).resolve()

    # Determine Python executable
    venv_python = root / ".venv" / "Scripts" / "python.exe"
    python_exe = venv_python if venv_python.exists() else Path(sys.executable)

    script_path = root / "run_digest_job.py"
    return root, python_exe, script_path


def build_task_config(
    task_name: str = DEFAULT_TASK_NAME,
    schedule_time: str = DEFAULT_SCHEDULE_TIME,
    project_dir: Path | None = None,
) -> TaskConfig:
    """Construct and validate a TaskConfig instance."""
    root, python_exe, script_path = get_default_paths(project_dir)
    return TaskConfig(
        task_name=task_name.strip(),
        schedule_time=schedule_time.strip(),
        python_exe=python_exe,
        script_path=script_path,
        working_dir=root,
    )


def build_powershell_install_command(config: TaskConfig) -> str:
    """Generate the idempotent PowerShell command to register or update the scheduled task."""
    py_exe_str = str(config.python_exe)
    script_str = str(config.script_path)
    work_dir_str = str(config.working_dir)

    return (
        f"$action = New-ScheduledTaskAction -Execute '{py_exe_str}' -Argument '\"{script_str}\"' -WorkingDirectory '{work_dir_str}'; "
        f"$trigger = New-ScheduledTaskTrigger -Daily -At '{config.schedule_time}'; "
        f"$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1); "
        f"Register-ScheduledTask -TaskName '{config.task_name}' -Action $action -Trigger $trigger -Settings $settings -Description 'Local Mail Agent Daily Digest Job' -Force"
    )


def build_powershell_uninstall_command(task_name: str = DEFAULT_TASK_NAME) -> str:
    """Generate the PowerShell command to cleanly unregister the task."""
    return f"Unregister-ScheduledTask -TaskName '{task_name.strip()}' -Confirm:$false -ErrorAction SilentlyContinue"


def build_powershell_status_command(task_name: str = DEFAULT_TASK_NAME) -> str:
    """Generate the PowerShell command to inspect task status and history."""
    clean_name = task_name.strip()
    return (
        f"$task = Get-ScheduledTask -TaskName '{clean_name}' -ErrorAction SilentlyContinue; "
        f"if ($task) {{ $info = Get-ScheduledTaskInfo -TaskName '{clean_name}'; "
        f"[PSCustomObject]@{{ TaskName = $task.TaskName; State = $task.State; LastRunTime = $info.LastRunTime; LastResult = $info.LastTaskResult; NextRunTime = $info.NextRunTime }} | Format-List }} "
        f"else {{ Write-Host 'Task \"{clean_name}\" is not registered.' }}"
    )


def build_powershell_run_command(task_name: str = DEFAULT_TASK_NAME) -> str:
    """Generate the PowerShell command to trigger the task immediately."""
    return f"Start-ScheduledTask -TaskName '{task_name.strip()}'"


DEFAULT_MONITOR_TASK_NAME = "LocalMailAgent-BackgroundMonitor"


def build_monitor_task_config(
    task_name: str = DEFAULT_MONITOR_TASK_NAME,
    project_dir: Path | None = None,
) -> TaskConfig:
    """Construct a TaskConfig for the independent background monitor service."""
    root = (project_dir or Path(__file__).resolve().parent).resolve()
    venv_python = root / ".venv" / "Scripts" / "python.exe"
    python_exe = venv_python if venv_python.exists() else Path(sys.executable)
    script_path = root / "monitor_service.py"
    return TaskConfig(
        task_name=task_name.strip(),
        schedule_time="00:00",
        python_exe=python_exe,
        script_path=script_path,
        working_dir=root,
    )


def build_monitor_powershell_install_command(config: TaskConfig) -> str:
    """Generate PowerShell command to register the continuous background monitor at user logon."""
    py_exe_str = str(config.python_exe)
    script_str = str(config.script_path)
    work_dir_str = str(config.working_dir)

    return (
        f"$action = New-ScheduledTaskAction -Execute '{py_exe_str}' -Argument '\"{script_str}\"' -WorkingDirectory '{work_dir_str}'; "
        f"$trigger = New-ScheduledTaskTrigger -AtLogOn; "
        f"$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5); "
        f"Register-ScheduledTask -TaskName '{config.task_name}' -Action $action -Trigger $trigger -Settings $settings -Description 'Local Mail Agent Continuous Background Monitoring Service' -Force"
    )

