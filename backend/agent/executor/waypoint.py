"""
Waypoint data structures for mission execution.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Literal
from enum import Enum

from config import (
    WAYPOINT_POSITION_TOLERANCE,
    WAYPOINT_ALTITUDE_TOLERANCE,
    WAYPOINT_VELOCITY_TOLERANCE,
    MAX_YAW_RATE,
)


class WaypointAction(str, Enum):
    """Waypoint action types."""
    MOVE_TO = "move_to"
    HOVER = "hover"
    LAND = "land"
    TAKEOFF = "takeoff"
    YAW = "yaw"


class WaypointStatus(str, Enum):
    """Waypoint execution status."""
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class Waypoint:
    """
    Waypoint data structure for mission execution.
    
    Attributes:
        waypoint_id: Unique identifier for the waypoint
        action: Action to perform (move_to, hover, land, takeoff, yaw)
        target_lat: Target latitude in degrees (for move_to)
        target_lon: Target longitude in degrees (for move_to)
        target_altitude: Target altitude in meters
        target_vx: Target velocity X (body frame, forward)
        target_vy: Target velocity Y (body frame, right)
        target_vz: Target velocity Z (body frame, down)
        target_yaw_rate: Target yaw rate in deg/s
        yaw_rate: Yaw rotation rate in deg/s (for yaw action)
        yaw_duration: Duration of yaw rotation in seconds (for yaw action)
        yaw_direction: Direction of yaw rotation ('left' or 'right')
        position_tolerance: Distance tolerance for position completion (meters)
        altitude_tolerance: Altitude tolerance for completion (meters)
        velocity_tolerance: Velocity tolerance for completion (m/s)
        hover_duration: Duration to hover in seconds (for hover action)
        description: Human-readable description
        estimated_duration: Estimated time to complete waypoint
        status: Current execution status
    """
    
    waypoint_id: str
    action: WaypointAction
    
    # Target position (for move_to)
    target_lat: Optional[float] = None
    target_lon: Optional[float] = None
    target_altitude: Optional[float] = None
    
    # Target velocity (for velocity-based movement)
    target_vx: Optional[float] = None
    target_vy: Optional[float] = None
    target_vz: Optional[float] = None
    target_yaw_rate: Optional[float] = None
    
    # Yaw action parameters
    yaw_rate: Optional[float] = None  # deg/s
    yaw_duration: Optional[float] = None  # seconds
    yaw_direction: Optional[Literal["left", "right"]] = None
    
    # Completion criteria
    position_tolerance: float = WAYPOINT_POSITION_TOLERANCE  # meters
    altitude_tolerance: float = WAYPOINT_ALTITUDE_TOLERANCE  # meters
    velocity_tolerance: float = WAYPOINT_VELOCITY_TOLERANCE  # m/s
    hover_duration: Optional[float] = None  # seconds
    
    # Metadata
    description: str = ""
    estimated_duration: Optional[float] = None
    status: WaypointStatus = field(default=WaypointStatus.PENDING)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Waypoint":
        """Create a Waypoint from a dictionary."""
        action_str = data.get("action", "move_to")
        action = WaypointAction(action_str) if isinstance(action_str, str) else action_str
        
        return cls(
            waypoint_id=data.get("waypoint_id", ""),
            action=action,
            target_lat=data.get("target_lat"),
            target_lon=data.get("target_lon"),
            target_altitude=data.get("target_altitude"),
            target_vx=data.get("target_vx"),
            target_vy=data.get("target_vy"),
            target_vz=data.get("target_vz"),
            target_yaw_rate=data.get("target_yaw_rate"),
            yaw_rate=data.get("yaw_rate"),
            yaw_duration=data.get("yaw_duration"),
            yaw_direction=data.get("yaw_direction"),
            position_tolerance=data.get("position_tolerance", WAYPOINT_POSITION_TOLERANCE),
            altitude_tolerance=data.get("altitude_tolerance", WAYPOINT_ALTITUDE_TOLERANCE),
            velocity_tolerance=data.get("velocity_tolerance", WAYPOINT_VELOCITY_TOLERANCE),
            hover_duration=data.get("hover_duration"),
            description=data.get("description", ""),
            estimated_duration=data.get("estimated_duration"),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "waypoint_id": self.waypoint_id,
            "action": self.action.value if isinstance(self.action, WaypointAction) else self.action,
            "target_lat": self.target_lat,
            "target_lon": self.target_lon,
            "target_altitude": self.target_altitude,
            "target_vx": self.target_vx,
            "target_vy": self.target_vy,
            "target_vz": self.target_vz,
            "target_yaw_rate": self.target_yaw_rate,
            "yaw_rate": self.yaw_rate,
            "yaw_duration": self.yaw_duration,
            "yaw_direction": self.yaw_direction,
            "position_tolerance": self.position_tolerance,
            "altitude_tolerance": self.altitude_tolerance,
            "velocity_tolerance": self.velocity_tolerance,
            "hover_duration": self.hover_duration,
            "description": self.description,
            "estimated_duration": self.estimated_duration,
            "status": self.status.value if isinstance(self.status, WaypointStatus) else self.status,
        }
    
    def is_position_waypoint(self) -> bool:
        """Check if this is a position-based waypoint."""
        return self.action == WaypointAction.MOVE_TO and self.target_lat is not None
    
    def is_velocity_waypoint(self) -> bool:
        """Check if this is a velocity-based waypoint."""
        return self.target_vx is not None or self.target_vy is not None
    
    def validate(self) -> tuple[bool, Optional[str]]:
        """
        Validate waypoint parameters.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        if self.action == WaypointAction.MOVE_TO:
            if self.target_lat is None or self.target_lon is None:
                return False, "move_to waypoint requires target_lat and target_lon"
            
            if not (-90 <= self.target_lat <= 90):
                return False, f"Invalid latitude: {self.target_lat}"
            
            if not (-180 <= self.target_lon <= 180):
                return False, f"Invalid longitude: {self.target_lon}"
        
        if self.action == WaypointAction.TAKEOFF:
            if self.target_altitude is not None and self.target_altitude < 1.0:
                return False, "Takeoff altitude must be at least 1.0 meters"
        
        if self.action == WaypointAction.HOVER:
            if self.hover_duration is not None and self.hover_duration <= 0:
                return False, "Hover duration must be positive"
        
        if self.action == WaypointAction.YAW:
            if self.yaw_rate is None:
                return False, "yaw waypoint requires yaw_rate"
            if self.yaw_duration is None:
                return False, "yaw waypoint requires yaw_duration"
            if abs(self.yaw_rate) > MAX_YAW_RATE:
                return False, f"yaw_rate ({self.yaw_rate}) exceeds maximum ({MAX_YAW_RATE})"
            if self.yaw_duration <= 0:
                return False, "yaw_duration must be positive"
            if self.yaw_direction and self.yaw_direction not in ["left", "right"]:
                return False, f"yaw_direction must be 'left' or 'right', got '{self.yaw_direction}'"
        
        return True, None
