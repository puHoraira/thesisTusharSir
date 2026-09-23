"""
Tools for mission management and execution control.
Provides mission progress, evaluation, and update capabilities.
"""

import json
import logging
from typing import List, Dict, Any, Optional, Literal
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from agent.tools.waypoint import meters_to_gps_offset

from agent.tools.base import ToolResult, ToolContext, tool_error_handler

logger = logging.getLogger("tool_mission")


@tool_error_handler
async def _get_mission_progress_impl(context: ToolContext) -> ToolResult:
    """Internal implementation of get_mission_progress."""
    
    mission_executor = context.mission_executor
    
    if not mission_executor:
        return ToolResult.fail("Mission executor not available")
    
    # Auto-get active mission ID
    mission_id = mission_executor._active_mission_id
    if not mission_id:
        return ToolResult.fail("No active mission.")
    
    # Get mission info from executor
    mission_info = mission_executor.get_mission_info(mission_id)
    
    if mission_info is None:
        return ToolResult.fail(f"Mission {mission_id} not found")
    
    waypoints = mission_info.get("waypoints", [])
    current_index = mission_info.get("current_index", 0)
    status = mission_info.get("status", "unknown")
    started_at = mission_info.get("started_at")
    
    # Calculate progress
    total_waypoints = len(waypoints)
    completed_count = current_index
    remaining_count = total_waypoints - current_index
    
    # Get current and next waypoint info
    current_waypoint = None
    next_waypoint = None
    
    if current_index < total_waypoints:
        current_waypoint = waypoints[current_index]
    if current_index + 1 < total_waypoints:
        next_waypoint = waypoints[current_index + 1]
    
    # Estimated completion based on AI-provided estimated_time
    estimated_remaining_seconds = None
    estimated_time = mission_info.get("estimated_time")
    if remaining_count > 0 and estimated_time is not None:
        # Calculate remaining time proportionally based on waypoints remaining
        if total_waypoints > 0:
            estimated_remaining_seconds = estimated_time * (remaining_count / total_waypoints)
    
    progress = {
        "mission_id": mission_id,
        "status": status,
        "current_waypoint_index": current_index,
        "total_waypoints": total_waypoints,
        "completed_count": completed_count,
        "remaining_count": remaining_count,
        "progress_percent": round((completed_count / total_waypoints * 100), 1) if total_waypoints > 0 else 0,
        "current_waypoint": current_waypoint,
        "next_waypoint": next_waypoint,
        "estimated_remaining_seconds": estimated_remaining_seconds,
        "is_paused": mission_executor.is_paused,
    }
    
    if started_at:
        progress["started_at"] = started_at
    
    logger.info(f"Mission progress: {mission_id} - {completed_count}/{total_waypoints} waypoints complete")
    
    return ToolResult.ok(progress)


def create_mission_progress_tool(context: ToolContext):
    """
    Create a get_mission_progress tool bound to a specific context.
    """
    
    @tool
    async def get_mission_progress_bound() -> str:
        """
        Get current mission execution status.
        
        Automatically detects the active mission - no parameters needed.
        
        Returns information about:
        - Mission ID
        - Current waypoint index
        - Completed/remaining waypoints
        - Progress percentage
        - Estimated time to completion
        - Whether mission is paused
        
        Returns:
            JSON string with mission progress details, or error if no active mission
        """
        result = await _get_mission_progress_impl(context)
        if result.success:
            return json.dumps(result.data)
        else:
            return json.dumps({"error": result.error})
    
    return get_mission_progress_bound


# ========== Evaluate and Update Mission Tool ==========

@tool_error_handler
async def _evaluate_and_update_mission_impl(
    action: str,
    waypoints: Optional[List[Dict[str, Any]]],
    context: ToolContext
) -> ToolResult:
    """
    Internal implementation of evaluate_and_update_mission.
    Handles mission control based on evaluation decision.
    """
    mission_executor = context.mission_executor
    
    if not mission_executor:
        return ToolResult.fail("Mission executor not available")
    
    # Get the active mission ID
    mission_id = mission_executor._active_mission_id
    if not mission_id:
        return ToolResult.fail("No active mission to evaluate")
    
    logger.info(f"Mission evaluation: action={action}")
    
    if action == "pause":
        # Pause the mission without resuming - allows user to decide next steps
        logger.info(f"Pausing mission {mission_id}")
        
        # Check if already paused
        if mission_executor.is_paused:
            return ToolResult.ok({
                "action": "pause",
                "mission_id": mission_id,
                "status": "already_paused",
                "message": "Mission was already paused"
            })
        
        # Pause the mission
        paused = mission_executor.pause_mission()
        
        if paused:
            return ToolResult.ok({
                "action": "pause",
                "mission_id": mission_id,
                "status": "mission_paused",
                "message": "Mission paused. Use 'continue' to resume, 'update' to modify waypoints, or 'abort' to stop."
            })
        else:
            return ToolResult.fail("Failed to pause mission - no mission is executing")
    
    elif action == "abort":
        # Stop the mission properly
        logger.info(f"Aborting mission {mission_id}")
        await mission_executor.stop_mission("Mission aborted by user/agent")
        
        return ToolResult.ok({
            "action": "abort",
            "mission_id": mission_id,
            "status": "mission_aborted"
        })
    
    elif action == "update":
        if not waypoints or len(waypoints) == 0:
            return ToolResult.fail("UPDATE action requires waypoints to be provided")
        
        logger.info(f"Updating mission {mission_id} with {len(waypoints)} new waypoints")
        
        # Get current drone position as reference for offset conversion
        # Use safety_manager which tracks current position from telemetry
        safety_manager = context.safety_manager
        
        reference_lat = None
        reference_lon = None
        
        if safety_manager:
            # First try current position (where the drone actually is now)
            if safety_manager.current_position:
                reference_lat, reference_lon, _ = safety_manager.current_position
                logger.debug(f"Using drone's current position as reference: ({reference_lat:.6f}, {reference_lon:.6f})")
            # Fallback to home position (GPS fix location)
            elif safety_manager.config.home_position:
                reference_lat, reference_lon, _ = safety_manager.config.home_position
                logger.info(f"Using home position as reference: ({reference_lat:.6f}, {reference_lon:.6f})")
        
        if reference_lat is None or reference_lon is None:
            return ToolResult.fail(
                "Cannot get drone position for waypoint conversion. "
                "Ensure drone has GPS fix before updating mission with relative offsets."
            )
        
        # Process waypoints - convert offsets to GPS coordinates
        processed_waypoints = []
        
        for i, wp in enumerate(waypoints):
            processed_wp = dict(wp)  # Copy the waypoint
            action_type = wp.get("action")
            
            if action_type == "move_to":
                target_lat = wp.get("target_lat")
                target_lon = wp.get("target_lon")
                offset_north = wp.get("offset_north")
                offset_east = wp.get("offset_east")
                
                # If absolute coordinates provided, use them and update reference
                if target_lat is not None and target_lon is not None:
                    reference_lat = target_lat
                    reference_lon = target_lon
                    logger.info(f"Update waypoint {i}: Using absolute coordinates ({target_lat:.6f}, {target_lon:.6f})")
                
                # If relative offsets provided, convert to GPS
                elif offset_north is not None or offset_east is not None:
                    offset_north = offset_north or 0.0
                    offset_east = offset_east or 0.0
                    
                    target_lat, target_lon = meters_to_gps_offset(
                        offset_north, offset_east, reference_lat, reference_lon
                    )
                    
                    processed_wp["target_lat"] = target_lat
                    processed_wp["target_lon"] = target_lon
                    # Remove the offsets since we've converted them
                    processed_wp.pop("offset_north", None)
                    processed_wp.pop("offset_east", None)
                    
                    logger.info(
                        f"Update waypoint {i}: Converted offsets (N:{offset_north}m, E:{offset_east}m) "
                        f"from reference ({reference_lat:.6f}, {reference_lon:.6f}) "
                        f"to target ({target_lat:.6f}, {target_lon:.6f})"
                    )
                else:
                    return ToolResult.fail(
                        f"Waypoint {i}: move_to requires either "
                        "(target_lat + target_lon) OR (offset_north + offset_east)"
                    )
            
            processed_waypoints.append(processed_wp)
        
        # Update the remaining waypoints with processed (converted) waypoints
        success = mission_executor.update_remaining_waypoints(mission_id, processed_waypoints)
        
        if not success:
            return ToolResult.fail(f"Failed to update waypoints for mission {mission_id}")
        
        # Resume the mission with updated waypoints
        mission_executor.resume_mission()
        
        return ToolResult.ok({
            "action": "update",
            "mission_id": mission_id,
            "new_waypoint_count": len(processed_waypoints),
            "status": "mission_updated_and_resumed"
        })
    
    else:  # continue
        logger.info(f"Continuing mission {mission_id}")
        
        # Resume the mission
        mission_executor.resume_mission()
        
        return ToolResult.ok({
            "action": "continue",
            "mission_id": mission_id,
            "status": "mission_resumed"
        })


def create_mission_control_tools(context: ToolContext):
    """
    Create mission control tools bound to a specific context.
    Returns the evaluate_and_update_mission tool.
    """
    
    @tool
    async def evaluate_and_update_mission_bound(
        action: Literal["pause", "continue", "abort", "update"],
        waypoints: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Control an active mission: pause, resume, modify, or abort.
        
        Use this tool to:
        1. PAUSE a mission when the user wants to modify or stop it mid-flight
        2. CONTINUE (resume) a paused mission
        3. UPDATE waypoints and resume (modify the remaining flight path)
        4. ABORT the mission entirely (emergency stop)
        
        IMPORTANT: Before calling this tool, use `send_message_to_user` to inform
        the user about your decision and reasoning.
        
        Actions:
        - "pause": Pause the mission (drone hovers). Use this when user wants to modify mid-mission.
        - "continue": Resume the mission with current waypoints (safe to proceed)
        - "abort": Stop the mission immediately (safety concern or user request)
        - "update": Modify remaining waypoints and resume (adjust path)
        
        WORKFLOW for user-requested mid-mission changes:
        1. User sends command during active mission (e.g., "change the pattern to...")
        2. Call pause action first: evaluate_and_update_mission(action="pause")
        3. Create new waypoints based on user request
        4. Call update action: evaluate_and_update_mission(action="update", waypoints=[...])
        
        For "update" action, you MUST provide new waypoints. Each waypoint should have:
        - action: "move_to", "hover", "land", "takeoff", or "yaw"
        - For move_to: offset_north, offset_east (meters from current reference), target_altitude
        - For yaw: yaw_rate, yaw_duration, yaw_direction ("left" or "right")
        - description: Brief description of the waypoint
        
        Example waypoints for returning home safely:
        [
            {"action": "move_to", "offset_north": 0, "offset_east": 0, "target_altitude": 5, "description": "Return to home"},
            {"action": "land", "description": "Land at home position"}
        ]
        
        Args:
            action: The action to take - "pause", "continue", "abort", or "update"
            waypoints: New waypoints (required only for "update" action)
            
        Returns:
            JSON string with the result of the action
        """
        result = await _evaluate_and_update_mission_impl(action, waypoints, context)
        if result.success:
            return json.dumps(result.data)
        else:
            return json.dumps({"error": result.error})
    
    return evaluate_and_update_mission_bound

