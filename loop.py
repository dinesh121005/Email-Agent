from collections.abc import Callable, Sequence
from typing import Any

from state import AgentState


class StepLimitExceeded(RuntimeError):
    """Raised when the execution exceeds the maximum allowed steps."""
    pass


def run_steps(
    steps: Sequence[Callable[[], Any]],
    state: AgentState,
    max_steps: int,
) -> Any:
    state.status = "running"

    try:
        for step in steps:
            if state.step_count >= max_steps:
                raise StepLimitExceeded(f"Step limit reached: maximum {max_steps} steps allowed")

            result = step()
            state.step_count += 1
            state.result = result

        state.status = "completed"
        return state.result

    except Exception as error:
        state.status = "failed"
        state.error = str(error)
        raise
