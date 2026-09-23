"""
Tool for getting current drone status.
Provides comprehensive drone state information to the LLM.
"""

import json
import logging
from typing import Any, Dict

from langchain_core.tools import tool

from agent.tools.base import ToolResult, ToolContext, tool_error_handler

logger = logging.getLogger("tool_drone_status")


@tool_error_handler
async def _get_drone_status_impl(context: ToolContext) -> ToolResult:
    """Internal implementation of get_drone_status."""
    
    drone_service = context.drone_service
    telemetry_service = context.telemetry_service
    safety_manager = context.safety_manager
    
    if not drone_service:
        return ToolResult.fail("Drone service not available")
    
    # Build status dictionary
    status = {
        "connected": drone_service.connected,
        "armed": safety_manager.is_armed if safety_manager else False,
        "offboard_active": drone_service.offboard_active,
        "offboard_initialized": drone_service.offboard_initialized,
    }
    
    # Position data from safety manager
    if safety_manager:
        # Get effective position (handles stale data)
        position, is_position_stale = safety_manager.get_effective_position()
        if position:
            lat, lon, alt = position
            status["position"] = {
                "latitude": lat,
                "longitude": lon,
                "altitude_absolute": alt,
                "altitude_relative": safety_manager.current_relative_altitude,
                "is_stale": is_position_stale,
            }
        
        # Home position for distance calculations
        home = safety_manager.config.home_position
        if home:
            home_lat, home_lon, home_alt = home
            status["home_position"] = {
                "latitude": home_lat,
                "longitude": home_lon,
                "altitude": home_alt,
            }
            # Distance from home (calculate if we have current position)
            if position:
                _, distance = safety_manager.check_distance(position[0], position[1])
                status["distance_from_home"] = round(distance, 2)
        
        # Battery info
        battery_level, is_battery_stale = safety_manager.get_effective_battery()
        if battery_level is not None:
            battery_safe, battery_msg = safety_manager.check_battery()
            status["battery"] = {
                "percentage": round(battery_level, 1),
                "is_critical": battery_msg == "critical",
                "is_warning": battery_msg == "warning",
                "is_stale": is_battery_stale,
            }
        
        # Safety status
        geofence_ok = True
        if position and home:
            geofence_ok, _ = safety_manager.check_distance(position[0], position[1])
        
        altitude_ok = True
        if safety_manager.current_relative_altitude > 0:
            altitude_ok, _ = safety_manager.check_altitude(safety_manager.current_relative_altitude)
        
        battery_ok = True
        if battery_level is not None:
            battery_ok, msg = safety_manager.check_battery()
            battery_ok = battery_ok or msg != "critical"
        
        status["safety"] = {
            "geofence_ok": geofence_ok,
            "altitude_ok": altitude_ok,
            "battery_ok": battery_ok,
            "max_altitude_limit": safety_manager.config.max_altitude,
            "max_distance_limit": safety_manager.config.max_distance,
            "min_battery_threshold": safety_manager.config.min_battery,
            "telemetry_health": safety_manager.get_telemetry_health(),
        }
    
    # GPS info
    if hasattr(drone_service, 'gps_info'):
        gps_info = drone_service.gps_info
        status["gps"] = {
            "healthy": gps_info.get("healthy", False),
            "fix_type": gps_info.get("fix_type"),
            "num_satellites": gps_info.get("num_satellites"),
        }
    
    # Flight mode from telemetry streams
    if telemetry_service and hasattr(telemetry_service, 'streams'):
        streams = telemetry_service.streams
        if hasattr(streams, '_latest_flight_mode') and streams._latest_flight_mode:
            mode = streams._latest_flight_mode
            status["flight_mode"] = mode.name if hasattr(mode, 'name') else str(mode)
    
    # Velocity limits
    if hasattr(drone_service, 'get_velocity_limits'):
        status["velocity_limits"] = drone_service.get_velocity_limits()
    
    logger.info(f"Drone status retrieved: connected={status.get('connected')}, armed={status.get('armed')}")
    
    return ToolResult.ok(status)


def create_drone_status_tool(context: ToolContext):
    """
    Create a get_drone_status tool bound to a specific context.
    
    This is used to inject the context into the tool for LangGraph.
    """
    @tool
    async def get_drone_status_bound() -> str:
        """
        Get current drone status including position, battery, flight mode, and safety status.
        
        Use this tool to understand the current state of the drone before planning missions
        or executing actions. Always check drone status before creating waypoint sequences.
        
        Returns:
            JSON string containing:
            - position: Current GPS position (lat, lon, altitude)
            - battery: Battery percentage and status
            - flight_mode: Current flight mode
            - armed: Whether the drone is armed
            - safety: Safety status including geofence, altitude limits
            - connected: Whether the drone is connected
        """
        result = await _get_drone_status_impl(context)
        if result.success:
            return json.dumps(result.data)
        else:
            return json.dumps({"error": result.error})
    
    return get_drone_status_bound
