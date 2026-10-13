import pytest

from retry import retry_call


def test_succeeds_after_two_timeouts():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1

        if calls < 3:
            raise TimeoutError("Temporary timeout")

        return "success"

    result = retry_call(
        operation,
        max_attempts=3,
        delay_seconds=0,
    )

    assert result == "success"
    assert calls == 3


def test_raises_after_max_attempts():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise TimeoutError("Still unavailable")

    with pytest.raises(TimeoutError):
        retry_call(
            operation,
            max_attempts=3,
            delay_seconds=0,
        )

    assert calls == 3
