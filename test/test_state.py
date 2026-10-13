from state import AgentState


def test_newly_created_state_starts_as_pending():
    state = AgentState(task_id="task-101")
    assert state.status == "pending"


def test_step_count_starts_at_zero():
    state = AgentState(task_id="task-101")
    assert state.step_count == 0


def test_result_and_error_start_as_none():
    state = AgentState(task_id="task-101")
    assert state.result is None
    assert state.error is None


def test_update_status_and_step_count():
    state = AgentState(task_id="task-101")

    state.status = "running"
    state.step_count = 1

    assert state.status == "running"
    assert state.step_count == 1

    state.status = "completed"
    state.step_count += 1
    state.result = "done"

    assert state.status == "completed"
    assert state.step_count == 2
    assert state.result == "done"
