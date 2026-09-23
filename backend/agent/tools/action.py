"""
Tool for executing immediate drone actions.
Provides direct control actions like takeoff, land, hover, yaw, and emergency stop.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent.tools.base import ToolResult, ToolContext, tool_error_handler
from config import MAX_YAW_RATE, MIN_TAKEOFF_ALTITUDE, SAFETY_MAX_ALTITUDE, MAX_YAW_RATE 

logger = logging.getLogger("tool_action")


class ExecuteActionInput(BaseModel):
    """Input schema for execute_action tool."""
    action: Literal["takeoff", "land", "hover", "emergency_stop", "yaw"] = Field(
        description="The action to execute: 'takeoff', 'land', 'hover', 'emergency_stop', or 'yaw'"
    )
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional parameters for the action. For takeoff: {'altitude': float}. For hover: {'duration': float}. For yaw: {'yaw_rate': float, 'duration': float, 'direction': 'left'|'right'}"
    )


@tool_error_handler
async def _execute_action_impl(
    action: str, 
    params: Dict[str, Any], 
    context: ToolContext
) -> ToolResult:
    """Internal implementation of execute_action."""
    
    drone_service = context.drone_service
    
    if not drone_service:
        return ToolResult.fail("Drone service not available")
    
    if not drone_service.connected:
        return ToolResult.fail("Drone not connected")
    
    action = action.lower()
    
    if action == "takeoff":
        altitude = params.get("altitude", 2.5)
        if altitude < MIN_TAKEOFF_ALTITUDE:
            return ToolResult.fail(f"Takeoff altitude must be at least {MIN_TAKEOFF_ALTITUDE} meters")
        if altitude > SAFETY_MAX_ALTITUDE:
            return ToolResult.fail(f"Takeoff altitude cannot exceed {SAFETY_MAX_ALTITUDE} meters")
        
        logger.info(f"Executing takeoff to {altitude}m")
        success, message = await drone_service.takeoff(altitude=altitude)
        
        return ToolResult.ok({
            "success": success,
            "action": "takeoff",
            "altitude": altitude,
            "message": message
        })
    
    elif action == "land":
        logger.info("Executing land")
        success, message = await drone_service.land()
        
        return ToolResult.ok({
            "success": success,
            "action": "land",
            "message": message
        })
    
    elif action == "hover":
        # Hover is implemented by sending zero velocity
        duration = params.get("duration")
        logger.info(f"Executing hover{' for ' + str(duration) + 's' if duration else ''}")
        
        success = await drone_service.send_zero_velocity()
        
        if not success:
            return ToolResult.fail("Failed to send hover command")
        
        # If duration is specified, wait for that duration while hovering
        if duration and duration > 0:
            logger.info(f"Hovering for {duration} seconds")
            start_time = asyncio.get_event_loop().time()
            remaining = duration
            while remaining > 0:
                await asyncio.sleep(0.5)
                elapsed = asyncio.get_event_loop().time() - start_time
                remaining = duration - elapsed
                if remaining > 0:
                    success = await drone_service.send_zero_velocity()
                    if not success:
                        return ToolResult.fail("Failed to send hover command")  

            return ToolResult.ok({
                "success": True,
                "action": "hover",
                "duration": round(duration, 2),
                "message": f"Hovered for {round(duration, 2)} seconds"
            })
        else:
            # No duration specified, hover indefinitely (until next command)
            return ToolResult.ok({
                "success": True,
                "action": "hover",
                "duration": None,
                "message": "Hovering at current position (indefinite)"
            })
    
    elif action == "yaw":
        # Yaw rotation - rotate the drone at specified yaw rate for specified duration
        yaw_rate = params.get("yaw_rate")
        duration = params.get("duration")
        direction = params.get("direction", "right")  # Default right (clockwise)
        
        # Validate required parameters
        if yaw_rate is None:
            return ToolResult.fail("yaw_rate parameter is required for yaw action")
        if duration is None:
            return ToolResult.fail("duration parameter is required for yaw action")
        
        # Validate yaw_rate is within limits
        if abs(yaw_rate) > MAX_YAW_RATE:
            return ToolResult.fail(f"yaw_rate ({yaw_rate} deg/s) exceeds maximum allowed ({MAX_YAW_RATE} deg/s)")
        
        # Validate duration is positive
        if duration <= 0:
            return ToolResult.fail(f"duration must be positive, got {duration}s")
        
        # Apply direction: positive yaw_rate = clockwise (right), negative = counter-clockwise (left)
        if direction.lower() == "left":
            yaw_rate = -abs(yaw_rate)  # Ensure negative for left
        elif direction.lower() == "right":
            yaw_rate = abs(yaw_rate)  # Ensure positive for right
        else:
            return ToolResult.fail(f"direction must be 'left' or 'right', got '{direction}'")
        
        # Calculate total angle for logging
        total_angle = abs(yaw_rate) * duration
        
        logger.info(f"Executing yaw: {total_angle:.1f} degrees to the {direction} (rate: {yaw_rate} deg/s, duration: {duration:.2f}s)")
        
        # Check if drone is armed/flying
        safety_manager = context.safety_manager
        if safety_manager and not safety_manager.is_armed:
            return ToolResult.fail("Cannot yaw: drone is not armed. Takeoff first.")
        
        # Execute yaw rotation using velocity command
        try:
            # Send yaw command (zero linear velocity, only yaw rate)
            success, message = await drone_service.set_velocity_body(0.0, 0.0, 0.0, yaw_rate)
            
            if not success:
                return ToolResult.fail(f"Failed to start yaw: {message}")
            
            # Use a robust sleep loop with small intervals to handle potential interruptions
            # This ensures the full duration is respected even if there are minor async issues
            start_time = asyncio.get_event_loop().time()
            remaining = duration
            
            while remaining > 0:
                # Sleep in small chunks (max 0.5 seconds) to check for cancellation
                # and to be more resilient to async scheduling issues
                sleep_time = min(0.5, remaining)

                await asyncio.sleep(sleep_time)
                
                elapsed = asyncio.get_event_loop().time() - start_time
                remaining = duration - elapsed
                
                # Re-send the yaw command periodically to ensure it's maintained
                # This helps if there are any setpoint timeouts
                if remaining > 0:
                    success, _ = await drone_service.set_velocity_body(0.0, 0.0, 0.0, yaw_rate)
                    if not success:
                        logger.warning("Failed to resend yaw velocity during rotation")
            
            # Stop yaw by sending zero velocity
            await drone_service.send_zero_velocity()
            
            actual_elapsed = asyncio.get_event_loop().time() - start_time
            actual_angle = abs(yaw_rate) * actual_elapsed
            logger.info(f"Yaw completed: {actual_angle:.1f} degrees in {actual_elapsed:.2f}s")
            
            return ToolResult.ok({
                "success": True,
                "action": "yaw",
                "yaw_rate": yaw_rate,
                "duration": round(actual_elapsed, 2),
                "direction": direction,
                "total_angle": round(actual_angle, 1),
                "message": f"Rotated {round(actual_angle, 1)} degrees to the {direction} at {yaw_rate} deg/s"
            })
        except Exception as e:
            # Ensure we stop on error
            await drone_service.send_zero_velocity()
            return ToolResult.fail(f"Yaw failed: {str(e)}")
    
    elif action == "emergency_stop":
        logger.warning("Executing emergency stop")
        success, message = await drone_service.emergency_stop()
        
        return ToolResult.ok({
            "success": success,
            "action": "emergency_stop",
            "message": message
        })
    
    else:
        return ToolResult.fail(f"Unknown action: {action}. Valid actions: takeoff, land, hover, yaw, emergency_stop")


def create_execute_action_tool(context: ToolContext):
    """
    Create an execute_action tool bound to a specific context.
    """
    @tool(args_schema=ExecuteActionInput)
    async def execute_action_bound(
        action: Literal["takeoff", "land", "hover", "yaw", "emergency_stop"],
        params: Dict[str, Any] = {}
    ) -> str:
        """
        Execute an immediate drone action (bypasses waypoint queue).
        
        Available actions:
        - takeoff: Arm and take off to specified altitude (default 2.5m)
          params: {"altitude": float} - target altitude in meters
        - land: Land the drone at current position
          params: {} - no parameters needed  
        - hover: Hold current position
          params: {"duration": float} - optional hover duration in seconds
        - yaw: Rotate the drone at specified yaw rate for specified duration
          params: {"yaw_rate": float, "duration": float, "direction": "left" | "right"} - yaw rate in deg/s, duration in seconds, direction of rotation
        - emergency_stop: Immediately stop all movement and land
          params: {} - no parameters needed
        
        Args:
            action: The action to execute
            params: Optional parameters for the action
            
        Returns:
            JSON string with success status and message
        """
        result = await _execute_action_impl(action, params, context)
        if result.success:
            return json.dumps(result.data)
        else:
            return json.dumps({"success": False, "error": result.error})
    
    return execute_action_bound
