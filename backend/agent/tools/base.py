"""
Base tool class for LLM tools.
Provides common functionality and interface for all drone control tools.
"""

import logging
from typing import Any, Dict, Optional, Callable
from functools import wraps

from pydantic import BaseModel, Field

logger = logging.getLogger("tool_base")


class ToolResult(BaseModel):
    """Standard result format for tool execution."""
    success: bool = Field(description="Whether the tool execution was successful")
    data: Optional[Dict[str, Any]] = Field(default=None, description="Result data if successful")
    error: Optional[str] = Field(default=None, description="Error message if failed")
    
    @classmethod
    def ok(cls, data: Dict[str, Any]) -> "ToolResult":
        """Create a successful result."""
        return cls(success=True, data=data)
    
    @classmethod
    def fail(cls, error: str) -> "ToolResult":
        """Create a failed result."""
        return cls(success=False, error=error)


class ToolContext:
    """
    Context object passed to tools during execution.
    Contains references to services and WebSocket connection.
    """
    
    def __init__(
        self,
        drone_service: Any = None,
        telemetry_service: Any = None,
        safety_manager: Any = None,
        mission_executor: Any = None,
        websocket: Any = None,
        session_id: Optional[str] = None,
    ):
        self.drone_service = drone_service
        self.telemetry_service = telemetry_service
        self.safety_manager = safety_manager
        self.mission_executor = mission_executor
        self.websocket = websocket
        self.session_id = session_id


def tool_error_handler(func: Callable) -> Callable:
    """
    Decorator for handling errors in tool execution.
    Catches exceptions and returns ToolResult with error info.
    """
    @wraps(func)
    async def wrapper(*args, **kwargs) -> ToolResult:
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Tool error in {func.__name__}: {e}", exc_info=True)
            return ToolResult.fail(str(e))
    return wrapper


# Type alias for tool functions
ToolFunction = Callable[..., ToolResult]
