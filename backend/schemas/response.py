"""
Response schemas for API responses.
"""

from pydantic import BaseModel


class CommandResponse(BaseModel):
    """Response model for control commands"""
    status: str  # "success" or "error"
    message: str

