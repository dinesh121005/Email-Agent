from collections.abc import Callable
from typing import Any


class ToolRegistryError(Exception):
    """Base exception for tool registry errors."""
    pass


class ToolNotFoundError(ToolRegistryError, KeyError):
    """Raised when attempting to access or execute an unregistered tool."""
    pass


class ToolAlreadyExistsError(ToolRegistryError, ValueError):
    """Raised when registering a tool with a name that is already registered."""
    pass


class ToolRegistry:
    """Registry for approved agent tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, func: Callable[..., Any] | None = None) -> Any:
        """Register a tool callable under a unique name.
        
        Can be used directly or as a decorator.
        """
        if func is None:
            def decorator(target_func: Callable[..., Any]) -> Callable[..., Any]:
                self.register(name, target_func)
                return target_func
            return decorator

        if name in self._tools:
            raise ToolAlreadyExistsError(f"Tool '{name}' is already registered")

        self._tools[name] = func

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        /,
        **kwargs: Any,
    ) -> Any:
        """Execute a registered tool by name with keyword arguments."""
        if tool_name not in self._tools:
            raise ToolNotFoundError(f"Tool '{tool_name}' is not registered")

        params: dict[str, Any] = {}
        if arguments is not None:
            params.update(arguments)
        params.update(kwargs)

        return self._tools[tool_name](**params)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> list[dict[str, str]]:
        """Return metadata for all registered tools."""
        return [{"name": name} for name in sorted(self._tools.keys())]

    @property
    def tools(self) -> dict[str, Callable[..., Any]]:
        return dict(self._tools)
