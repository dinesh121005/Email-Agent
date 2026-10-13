"""Unit tests for Windows Task Scheduler configuration and command builders."""

from pathlib import Path
import pytest

from task_scheduler import (
    DEFAULT_SCHEDULE_TIME,
    DEFAULT_TASK_NAME,
    TaskConfig,
    build_powershell_install_command,
    build_powershell_run_command,
    build_powershell_status_command,
    build_powershell_uninstall_command,
    build_task_config,
    get_default_paths,
)


def test_build_task_config_with_defaults(tmp_path: Path) -> None:
    config = build_task_config(project_dir=tmp_path)

    assert config.task_name == DEFAULT_TASK_NAME
    assert config.schedule_time == DEFAULT_SCHEDULE_TIME
    assert config.working_dir == tmp_path
    assert config.script_path == tmp_path / "run_digest_job.py"


def test_build_task_config_custom_time(tmp_path: Path) -> None:
    config = build_task_config(
        task_name="CustomDigest",
        schedule_time="09:15",
        project_dir=tmp_path,
    )
    assert config.task_name == "CustomDigest"
    assert config.schedule_time == "09:15"


def test_task_config_validates_time_format(tmp_path: Path) -> None:
    # Invalid time formats
    with pytest.raises(ValueError, match="24-hour format"):
        build_task_config(schedule_time="8:00", project_dir=tmp_path)

    with pytest.raises(ValueError, match="24-hour format"):
        build_task_config(schedule_time="25:00", project_dir=tmp_path)

    with pytest.raises(ValueError, match="24-hour format"):
        build_task_config(schedule_time="12:60", project_dir=tmp_path)

    with pytest.raises(ValueError, match="24-hour format"):
        build_task_config(schedule_time="invalid", project_dir=tmp_path)


def test_task_config_validates_task_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="task_name"):
        build_task_config(task_name="   ", project_dir=tmp_path)


def test_build_powershell_install_command_contains_all_requirements(tmp_path: Path) -> None:
    config = build_task_config(
        task_name="TestDigest",
        schedule_time="07:30",
        project_dir=tmp_path,
    )
    cmd = build_powershell_install_command(config)

    # Check key Task Scheduler parameters
    assert "New-ScheduledTaskAction" in cmd
    assert str(config.script_path) in cmd
    assert str(config.working_dir) in cmd
    assert "At '07:30'" in cmd
    assert "-StartWhenAvailable" in cmd
    assert "-MultipleInstances IgnoreNew" in cmd
    assert "-AllowStartIfOnBatteries" in cmd
    assert "-DontStopIfGoingOnBatteries" in cmd
    assert "-Force" in cmd
    assert "Register-ScheduledTask -TaskName 'TestDigest'" in cmd


def test_build_powershell_uninstall_command() -> None:
    cmd = build_powershell_uninstall_command("TestDigest")
    assert "Unregister-ScheduledTask -TaskName 'TestDigest'" in cmd
    assert "-Confirm:$false" in cmd


def test_build_powershell_status_command() -> None:
    cmd = build_powershell_status_command("TestDigest")
    assert "Get-ScheduledTask -TaskName 'TestDigest'" in cmd
    assert "Get-ScheduledTaskInfo -TaskName 'TestDigest'" in cmd


def test_build_powershell_run_command() -> None:
    cmd = build_powershell_run_command("TestDigest")
    assert "Start-ScheduledTask -TaskName 'TestDigest'" in cmd
