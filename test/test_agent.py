
import pytest

from agent import run_task


def test_successful_task():
    result = run_task(lambda: "email classified")

    assert result == "email classified"


def test_failed_task_propagates_error():
    def failing_operation():
        raise ValueError("Invalid input")

    with pytest.raises(ValueError, match="Invalid input"):
        run_task(failing_operation)
