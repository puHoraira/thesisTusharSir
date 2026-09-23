"""
State types for the LangGraph agent.
Defines the AgentState TypedDict and related types.
"""

from typing import TypedDict, Optional, List, Dict, Any, Literal, Annotated
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from langgraph.graph import add_messages


class AgentStateEnum(str, Enum):
    """Enum for agent state machine states.
    
    Note: Mission execution is handled externally by MissionExecutor,
    not by the graph, so EXECUTE_MISSION and UPDATE_PLAN are removed.
    """
    IDLE = "idle"
    RECEIVE_COMMAND = "receive_command"
    PLAN_MISSION = "plan_mission"
    CONFIRM_PLAN = "confirm_plan"
    EVALUATE_MISSION = "evaluate_mission"  # LLM evaluates mission during execution
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class DroneStatus:
    """Current drone status information."""
    connected: bool = False
    armed: bool = False
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude_relative: Optional[float] = None
    altitude_absolute: Optional[float] = None
    battery_percentage: Optional[float] = None
    flight_mode: Optional[str] = None
    distance_from_home: Optional[float] = None
    gps_healthy: bool = False
    offboard_active: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "connected": self.connected,
            "armed": self.armed,
            "position": {
                "latitude": self.latitude,
                "longitude": self.longitude,
                "altitude_relative": self.altitude_relative,
                "altitude_absolute": self.altitude_absolute,
            },
            "battery_percentage": self.battery_percentage,
            "flight_mode": self.flight_mode,
            "distance_from_home": self.distance_from_home,
            "gps_healthy": self.gps_healthy,
            "offboard_active": self.offboard_active,
        }


@dataclass
class MissionState:
    """Current mission execution state."""
    mission_id: Optional[str] = None
    status: Literal["pending", "confirmed", "executing", "paused", "completed", "aborted", "error"] = "pending"
    current_waypoint_index: int = 0
    total_waypoints: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "mission_id": self.mission_id,
            "status": self.status,
            "current_waypoint_index": self.current_waypoint_index,
            "total_waypoints": self.total_waypoints,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
        }


@dataclass
class Waypoint:
    """Waypoint data structure."""
    waypoint_id: str
    action: Literal["move_to", "hover", "land", "takeoff", "yaw"]
    target_lat: Optional[float] = None
    target_lon: Optional[float] = None
    target_altitude: Optional[float] = None
    hover_duration: Optional[float] = None
    # Yaw parameters
    yaw_rate: Optional[float] = None  # degrees per second
    yaw_duration: Optional[float] = None  # seconds
    yaw_direction: Optional[Literal["left", "right"]] = None
    description: str = ""
    position_tolerance: float = 0.5
    altitude_tolerance: float = 0.3
    velocity_tolerance: float = 0.1
    estimated_duration: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "waypoint_id": self.waypoint_id,
            "action": self.action,
            "target_lat": self.target_lat,
            "target_lon": self.target_lon,
            "target_altitude": self.target_altitude,
            "hover_duration": self.hover_duration,
            "yaw_rate": self.yaw_rate,
            "yaw_duration": self.yaw_duration,
            "yaw_direction": self.yaw_direction,
            "description": self.description,
            "position_tolerance": self.position_tolerance,
            "altitude_tolerance": self.altitude_tolerance,
        }


class AgentState(TypedDict, total=False):
    """
    Main agent state for LangGraph.
    
    This TypedDict defines all the state fields that are passed between
    nodes in the LangGraph state machine.
    
    Note: Mission execution state (waypoint tracking) is managed by
    MissionExecutor, not in this state.
    """
    
    # ========== User Input ==========
    user_command: str  # The current user command being processed
    
    # ========== Drone Status ==========
    current_status: Dict[str, Any]  # Current drone status from get_drone_status
    essential_status: Optional[str]  # Essential status string to include in user messages (reduces tool calls)
    
    # ========== Mission State ==========
    mission_state: Dict[str, Any]  # Current mission execution state (summary info)
    
    # ========== LLM Interaction ==========
    messages: Annotated[List[Any], add_messages]  # LangChain message history with proper accumulation
    llm_response: Optional[str]  # Latest LLM response text
    
    # ========== Control Flags ==========
    current_state: str  # Current state machine state (AgentStateEnum value)
    mission_active: bool  # Whether a mission is currently executing
    awaiting_user_input: bool  # Waiting for user to send new command
    awaiting_plan_confirmation: bool  # Waiting for user to confirm/abort plan
    
    # ========== Plan Confirmation (Human-in-the-Loop) ==========
    plan_pending_confirmation: Optional[Dict[str, Any]]  # Plan data awaiting confirmation
    plan_confirmed: Optional[bool]  # True if confirmed, False if aborted
    user_feedback: Optional[str]  # Feedback from user if they abort
    
    # ========== Error State ==========
    error_state: Optional[str]  # Error message if in error state
    
    # ========== Mission Evaluation (triggered during execution) ==========
    evaluation_triggered: bool  # Flag indicating mission evaluation is needed
    evaluation_mission_id: Optional[str]  # Mission ID to evaluate
    evaluation_reason: Optional[str]  # Reason for evaluation (safety condition)
    
    # ========== Session Info ==========
    session_id: str  # Unique session identifier for this conversation


def create_initial_state(session_id: str) -> AgentState:
    """
    Create an initial agent state with default values.
    
    Args:
        session_id: Unique identifier for this session
        
    Returns:
        Initial AgentState dictionary
    """
    return {
        # User Input
        "user_command": "",
        
        # Drone Status
        "current_status": {},
        "essential_status": None,
        
        # Mission State
        "mission_state": {},
        
        # LLM Interaction
        "messages": [],
        "llm_response": None,
        
        # Control Flags
        "current_state": AgentStateEnum.IDLE.value,
        "mission_active": False,
        "awaiting_user_input": True,
        "awaiting_plan_confirmation": False,
        
        # Plan Confirmation
        "plan_pending_confirmation": None,
        "plan_confirmed": None,
        "user_feedback": None,
        
        # Error State
        "error_state": None,
        
        # Mission Evaluation
        "evaluation_triggered": False,
        "evaluation_mission_id": None,
        "evaluation_reason": None,
        
        # Session Info
        "session_id": session_id,
    }
