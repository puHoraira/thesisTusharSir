"""
Telemetry schemas for WebSocket telemetry messages.
"""

from pydantic import BaseModel
from typing import Optional


class PositionData(BaseModel):
    """Position telemetry data"""
    latitude: float
    longitude: float
    altitude_relative: float
    altitude_absolute: float


class BatteryData(BaseModel):
    """Battery telemetry data"""
    percentage: float
    voltage: float
    current: float

class TelemetryMessage(BaseModel):
    """Base telemetry message"""
    type: str  # "telemetry", "battery", "flight_mode"
    timestamp: str
    position: Optional[PositionData] = None
    battery: Optional[BatteryData] = None
    flight_mode: Optional[str] = None
    armed: Optional[bool] = None

