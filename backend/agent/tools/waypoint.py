"""
Tools for creating and updating waypoint sequences.
Manages mission planning for the drone.
"""

import json
import logging
import math
import uuid
from typing import Any, Dict, List, Optional, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent.tools.base import ToolResult, ToolContext, tool_error_handler
from config import METERS_PER_DEGREE_LAT, MIN_TAKEOFF_ALTITUDE, SAFETY_MAX_ALTITUDE, WAYPOINT_POSITION_TOLERANCE, MAX_YAW_RATE


logger = logging.getLogger("tool_waypoint")


def meters_to_gps_offset(
    offset_north: float, 
    offset_east: float, 
    reference_lat: float, 
    reference_lon: float
) -> tuple[float, float]:
    """
    Convert meter offsets to GPS coordinate offsets.
    
    Args:
        offset_north: Offset in meters (positive = north, negative = south)
        offset_east: Offset in meters (positive = east, negative = west)
        reference_lat: Reference latitude in degrees
        reference_lon: Reference longitude in degrees
        
    Returns:
        Tuple of (target_lat, target_lon)
    """
    # Latitude offset (1 degree ≈ 111,320 meters)
    lat_offset = offset_north / METERS_PER_DEGREE_LAT
    
    # Longitude offset (depends on latitude due to Earth's curvature)
    # At the equator, 1 degree longitude ≈ 111,320 meters
    # At latitude θ, 1 degree longitude ≈ 111,320 * cos(θ) meters
    meters_per_degree_lon = METERS_PER_DEGREE_LAT * math.cos(math.radians(reference_lat))
    lon_offset = offset_east / meters_per_degree_lon
    
    target_lat = reference_lat + lat_offset
    target_lon = reference_lon + lon_offset
    
    return target_lat, target_lon


class WaypointInput(BaseModel):
    """Schema for a single waypoint."""
    action: Literal["move_to", "hover", "land", "takeoff", "yaw"] = Field(
        description="The action to perform at this waypoint"
    )
    # Option 1: Absolute GPS coordinates
    target_lat: Optional[float] = Field(
        default=None, 
        description="Target latitude in degrees (for move_to action). Use offset_north/offset_east instead for relative positioning."
    )
    target_lon: Optional[float] = Field(
        default=None, 
        description="Target longitude in degrees (for move_to action). Use offset_north/offset_east instead for relative positioning."
    )
    # Option 2: Relative offsets in meters from REFERENCE position
    offset_north: Optional[float] = Field(
        default=None,
        description="Offset in meters. Positive = north, negative = south. Relative to the sequence start or the last absolute waypoint."
    )
    offset_east: Optional[float] = Field(
        default=None,
        description="Offset in meters. Positive = east, negative = west. Relative to the sequence start or the last absolute waypoint."
    )
    target_altitude: Optional[float] = Field(
        default=None, 
        description="Target altitude in meters (for move_to or takeoff)"
    )
    hover_duration: Optional[float] = Field(
        default=None, 
        description="Duration to hover in seconds (for hover action)"
    )
    # Yaw action parameters
    yaw_rate: Optional[float] = Field(
        default=None,
        description="Rotation rate in degrees per second (for yaw action)"
    )
    yaw_duration: Optional[float] = Field(
        default=None,
        description="Duration of yaw rotation in seconds (for yaw action)"
    )
    yaw_direction: Optional[Literal["left", "right"]] = Field(
        default=None,
        description="Direction of yaw rotation: 'left' (counter-clockwise) or 'right' (clockwise)"
    )
    description: str = Field(
        default="", 
        description="Human-readable description of this waypoint"
    )
    position_tolerance: float = Field(
        default=WAYPOINT_POSITION_TOLERANCE,
        description=f"Position tolerance in meters for waypoint completion"
    )


class CreateWaypointSequenceInput(BaseModel):
    """Input schema for create_waypoint_sequence tool."""
    waypoints: List[WaypointInput] = Field(
        description="List of waypoint dictionaries with action, position, and other parameters"
    )
    execution_mode: Literal["sequential", "parallel"] = Field(
        default="sequential",
        description="How to execute waypoints: 'sequential' (one by one) or 'parallel' (future)"
    )


@tool_error_handler
async def _create_waypoint_sequence_impl(
    waypoints: List[WaypointInput],
    execution_mode: str,
    context: ToolContext
) -> ToolResult:
    """Internal implementation of create_waypoint_sequence."""
    
    mission_executor = context.mission_executor
    safety_manager = context.safety_manager
    
    if not waypoints:
        return ToolResult.fail("No waypoints provided")
    
    # Get reference position for relative offset calculations
    # Priority: 1. current drone position (where it actually is), 2. home_position (fallback)
    reference_lat = None
    reference_lon = None
    reference_source = None
    
    if safety_manager:
        # First try current position (where the drone actually is now)
        if safety_manager.current_position:
            reference_lat, reference_lon, _ = safety_manager.current_position
            reference_source = "current_position"
            logger.debug(f"Using drone's current position as reference: ({reference_lat:.6f}, {reference_lon:.6f})")
        # Fallback to home position (GPS fix location)
        elif safety_manager.config.home_position:
            reference_lat, reference_lon, _ = safety_manager.config.home_position
            reference_source = "home_position"
            logger.info(
                f"Current position not available, using home position as reference: "
                f"({reference_lat:.6f}, {reference_lon:.6f})"
            )
    
    # Generate mission ID
    mission_id = f"mission_{uuid.uuid4().hex[:8]}"
    
    # Validate and process waypoints
    validated_waypoints = []
    
    # Track the reference position for relative offsets
    # By default, this is the home or current position
    # But if the first waypoint is an absolute coordinate, it becomes the new reference for subsequent relative moves
    current_reference_lat = reference_lat
    current_reference_lon = reference_lon
    
    for i, wp_model in enumerate(waypoints):
        # Convert pydantic model to dict for processing
        wp = wp_model.model_dump()
        action = wp.get("action", "move_to")
        
        # Validate action
        if action not in ["move_to", "hover", "land", "takeoff", "yaw"]:
            return ToolResult.fail(f"Waypoint {i}: Invalid action '{action}'")
        
        # For move_to, validate and process position
        target_lat = wp.get("target_lat")
        target_lon = wp.get("target_lon")
        
        if action == "move_to":
            offset_north = wp.get("offset_north")
            offset_east = wp.get("offset_east")
            
            # CASE 1: Absolute GPS coordinates provided
            if target_lat is not None and target_lon is not None:
                # Use provided coordinates
                logger.info(
                    f"🎯 [WAYPOINT-{i}] ABSOLUTE GPS:\n"
                    f"   Target GPS: ({target_lat:.7f}, {target_lon:.7f})\n"
                    f"   Description: {wp.get('description', 'N/A')}"
                )
                
                # Update reference for subsequent relative waypoints
                # If the user says "Go to X (absolute), then fly 10m North (relative)", 
                # the 10m North should be relative to X.
                current_reference_lat = target_lat
                current_reference_lon = target_lon
                
            # CASE 2: Relative offsets provided
            elif offset_north is not None or offset_east is not None:
                if current_reference_lat is None or current_reference_lon is None:
                    return ToolResult.fail(
                        f"Waypoint {i}: Cannot use relative offsets (offset_north/offset_east) - "
                        "no reference position available. The drone needs either a GPS fix (home position), "
                        "a known last position from a previous mission, or start with an absolute coordinate waypoint."
                    )
                
                # Default to 0 if only one offset provided
                offset_north = offset_north or 0.0
                offset_east = offset_east or 0.0
                
                # Convert meter offsets to GPS coordinates based on CURRENT REFERENCE
                target_lat, target_lon = meters_to_gps_offset(
                    offset_north, offset_east, current_reference_lat, current_reference_lon
                )
                
                logger.info(
                    f"🎯 [WAYPOINT-{i}] OFFSET CONVERSION:\n"
                    f"   Reference Point: ({current_reference_lat:.7f}, {current_reference_lon:.7f})\n"
                    f"   Offset: North={offset_north}m, East={offset_east}m\n"
                    f"   → Target GPS: ({target_lat:.7f}, {target_lon:.7f})\n"
                    f"   Description: {wp.get('description', 'N/A')}"
                )
                
                
            
            # Validate that we have coordinates
            if target_lat is None or target_lon is None:
                return ToolResult.fail(
                    f"Waypoint {i}: move_to requires either "
                    "(target_lat + target_lon) OR (offset_north + offset_east)"
                )
            
            # Validate against safety limits if safety manager available
            # Note: We check against HOME for geofence, regardless of how we got the coordinate
            if safety_manager:
                target_alt = wp.get("target_altitude", 5.0) or 5.0
                if target_alt > safety_manager.config.max_altitude:
                    return ToolResult.fail(
                        f"Waypoint {i}: altitude {target_alt}m exceeds max altitude limit of {safety_manager.config.max_altitude}m"
                    )
                
                # Check geofence (distance from actual home)
                is_safe, distance = safety_manager.check_distance(target_lat, target_lon)
                if not is_safe:
                    return ToolResult.fail(
                        f"Waypoint {i}: position ({target_lat:.6f}, {target_lon:.6f}) is {distance:.1f}m from home, "
                        f"exceeds geofence limit of {safety_manager.config.max_distance}m"
                    )
        
        # For takeoff, validate altitude
        if action == "takeoff":
            altitude = wp.get("target_altitude", 2.5) or 2.5
            if altitude < MIN_TAKEOFF_ALTITUDE:
                return ToolResult.fail(f"Waypoint {i}: takeoff altitude must be at least 1.0m")
            if safety_manager and altitude > SAFETY_MAX_ALTITUDE:
                return ToolResult.fail(
                    f"Waypoint {i}: takeoff altitude {altitude}m exceeds max limit of {safety_manager.config.max_altitude}m"
                )
        
        # For hover, validate duration
        if action == "hover":
            duration = wp.get("hover_duration")
            if duration is not None and duration <= 0:
                return ToolResult.fail(f"Waypoint {i}: hover duration must be positive")
        
        # For yaw, validate parameters
        if action == "yaw":
            yaw_rate = wp.get("yaw_rate")
            yaw_duration = wp.get("yaw_duration")
            yaw_direction = wp.get("yaw_direction", "right")
            
            if yaw_rate is None:
                return ToolResult.fail(f"Waypoint {i}: yaw action requires yaw_rate")
            if yaw_duration is None:
                return ToolResult.fail(f"Waypoint {i}: yaw action requires yaw_duration")
            if abs(yaw_rate) > MAX_YAW_RATE:
                return ToolResult.fail(
                    f"Waypoint {i}: yaw_rate ({yaw_rate}) exceeds maximum ({MAX_YAW_RATE} deg/s)"
                )
            if yaw_duration <= 0:
                return ToolResult.fail(f"Waypoint {i}: yaw_duration must be positive")
            if yaw_direction not in ["left", "right"]:
                return ToolResult.fail(f"Waypoint {i}: yaw_direction must be 'left' or 'right'")
        
        # Create validated waypoint with resolved GPS coordinates
        validated_wp = {
            "waypoint_id": f"wp_{i}",
            "action": action,
            "target_lat": target_lat,
            "target_lon": target_lon,
            "target_altitude": wp.get("target_altitude", 10.0) if action in ["move_to", "takeoff"] else None,
            "hover_duration": wp.get("hover_duration"),
            "yaw_rate": wp.get("yaw_rate"),
            "yaw_duration": wp.get("yaw_duration"),
            "yaw_direction": wp.get("yaw_direction", "right") if action == "yaw" else None,
            "description": wp.get("description", f"Waypoint {i}"),
            "position_tolerance": wp.get("position_tolerance", 1.0),
            "altitude_tolerance": wp.get("altitude_tolerance", 0.5),
        }
        validated_waypoints.append(validated_wp)
    
    # Store waypoints in mission executor if available
    if mission_executor:
        mission_executor.set_waypoints(mission_id, validated_waypoints)
    
    logger.info(f"Created waypoint sequence: mission_id={mission_id}, waypoints={len(validated_waypoints)}")
    
    return ToolResult.ok({
        "mission_id": mission_id,
        "waypoint_count": len(validated_waypoints),
        "waypoints": validated_waypoints,
        "execution_mode": execution_mode,
        "status": "pending_confirmation"
    })


def create_waypoint_tools(context: ToolContext):
    """
    Create waypoint tools bound to a specific context.
    Returns the create_waypoint_sequence tool.
    """
    
    @tool(args_schema=CreateWaypointSequenceInput)
    async def create_waypoint_sequence_bound(
        waypoints: List[WaypointInput],
        execution_mode: str = "sequential"
    ) -> str:
        """
        Create a mission waypoint sequence. REQUIRED: waypoints list.
        
        Args:
            waypoints: REQUIRED list of waypoint dicts. Each must have:
                - action: "takeoff" | "move_to" | "hover" | "yaw" | "land"
                - For takeoff: target_altitude (meters)
                - For move_to: offset_north, offset_east (meters), target_altitude
                - For hover: hover_duration (seconds)
                - For yaw: yaw_rate (deg/s), yaw_duration (s), yaw_direction ("left"/"right")
                - For land: no extra parameters needed
                - description: human-readable text (required)
            execution_mode: "sequential" (default)
        
        Example square path:
        [
            {"action": "takeoff", "target_altitude": 5, "description": "Take off"},
            {"action": "move_to", "offset_north": 10, "offset_east": 0, "target_altitude": 5, "description": "North"},
            {"action": "move_to", "offset_north": 10, "offset_east": 10, "target_altitude": 5, "description": "NE"},
            {"action": "move_to", "offset_north": 0, "offset_east": 0, "target_altitude": 5, "description": "Home"},
            {"action": "land", "description": "Land"}
        ]
        
        Returns: JSON with mission_id and waypoint details
        """
        result = await _create_waypoint_sequence_impl(waypoints, execution_mode, context)
        if result.success:
            return json.dumps(result.data)
        else:
            return json.dumps({"error": result.error})
    
    return create_waypoint_sequence_bound
