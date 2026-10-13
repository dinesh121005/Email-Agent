import pytest

from tools import (
    ToolAlreadyExistsError,
    ToolNotFoundError,
    ToolRegistry,
)


def test_register_and_execute_valid_tool():
    registry = ToolRegistry()

    def classify_email(subject: str, sender: str) -> str:
        return f"classified: {subject} from {sender}"

    registry.register("classify_email", classify_email)

    result = registry.execute(
        "classify_email",
        subject="Meeting Notes",
        sender="boss@example.com",
    )

    assert result == "classified: Meeting Notes from boss@example.com"


def test_execute_unknown_tool_raises_error():
    registry = ToolRegistry()

    with pytest.raises(ToolNotFoundError, match="Tool 'nonexistent' is not registered"):
        registry.execute("nonexistent")


def test_register_duplicate_name_raises_error():
    registry = ToolRegistry()
    registry.register("save_analysis", lambda: "first")

    with pytest.raises(ToolAlreadyExistsError, match="Tool 'save_analysis' is already registered"):
        registry.register("save_analysis", lambda: "second")


def test_tool_exception_propagates():
    registry = ToolRegistry()

    def failing_tool():
        raise RuntimeError("External service unavailable")

    registry.register("failing_tool", failing_tool)

    with pytest.raises(RuntimeError, match="External service unavailable"):
        registry.execute("failing_tool")


def test_register_decorator_syntax():
    registry = ToolRegistry()

    @registry.register("get_unprocessed_emails")
    def get_emails(limit: int = 10):
        return [f"email_{i}" for i in range(limit)]

    assert "get_unprocessed_emails" in registry
    assert registry.execute("get_unprocessed_emails", limit=2) == ["email_0", "email_1"]
