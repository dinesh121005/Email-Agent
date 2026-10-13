import pytest

from loop import StepLimitExceeded, run_steps
from state import AgentState


def test_two_steps_execute_in_order_and_return_final_result():
    order = []
    state = AgentState(task_id="task-1")

    def step_one():
        assert state.status == "running"
        order.append(1)
        return "first"

    def step_two():
        assert state.status == "running"
        order.append(2)
        return "second"

    result = run_steps([step_one, step_two], state, max_steps=5)

    assert result == "second"
    assert order == [1, 2]
    assert state.status == "completed"
    assert state.step_count == 2
    assert state.result == "second"
    assert state.error is None


def test_step_fails_state_becomes_failed_and_exception_propagates():
    state = AgentState(task_id="task-2")
    executed = []

    def step_one():
        executed.append("step_one")
        return "first"

    def step_two():
        executed.append("step_two")
        raise ValueError("Something went wrong")

    def step_three():
        executed.append("step_three")
        return "third"

    with pytest.raises(ValueError, match="Something went wrong"):
        run_steps([step_one, step_two, step_three], state, max_steps=5)

    assert executed == ["step_one", "step_two"]
    assert state.status == "failed"
    assert state.step_count == 1
    assert state.result == "first"
    assert state.error == "Something went wrong"


def test_step_limit_prevents_additional_execution():
    state = AgentState(task_id="task-3")
    executed = []

    def step_one():
        executed.append("step_one")
        return "first"

    def step_two():
        executed.append("step_two")
        return "second"

    def step_three():
        executed.append("step_three")
        return "third"

    with pytest.raises(StepLimitExceeded, match="Step limit reached"):
        run_steps([step_one, step_two, step_three], state, max_steps=2)

    assert executed == ["step_one", "step_two"]
    assert state.status == "failed"
    assert state.step_count == 2
    assert state.result == "second"
    assert "Step limit reached" in state.error


def test_zero_max_steps_prevents_any_execution():
    state = AgentState(task_id="task-4")
    executed = []

    def step_one():
        executed.append("step_one")
        return "first"

    with pytest.raises(StepLimitExceeded):
        run_steps([step_one], state, max_steps=0)

    assert executed == []
    assert state.status == "failed"
    assert state.step_count == 0
    assert state.result is None


def test_empty_steps_list_completes():
    state = AgentState(task_id="task-5")
    result = run_steps([], state, max_steps=3)

    assert result is None
    assert state.status == "completed"
    assert state.step_count == 0
    assert state.result is None
    assert state.error is None
