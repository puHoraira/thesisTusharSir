"""
Control command schemas.
"""

from pydantic import BaseModel


class ControlCommand(BaseModel):
    """Control command model for WebSocket messages"""
    type: str  # "takeoff", "land", "emergency_stop", "velocity_body"
    vx: float = 0.0  # forward (normalized -1 to 1)
    vy: float = 0.0  # right (normalized -1 to 1)
    vz: float = 0.0  # down (normalized -1 to 1)
    yaw_rate: float = 0.0  # clockwise (normalized -1 to 1)
    altitude: float = 10.0  # for takeoff

