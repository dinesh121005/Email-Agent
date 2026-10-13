from dataclasses import dataclass
from typing import Any


@dataclass
class AgentState:
    task_id: str
    status: str = "pending"
    step_count: int = 0
    result: Any = None
    error: str | None = None
