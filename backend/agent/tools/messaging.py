"""
Tools for sending messages to users via WebSocket.
Handles user communication and plan confirmation.
"""

import logging
import json
from typing import Any, Dict, Optional, Literal
from datetime import datetime

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent.tools.base import ToolResult, ToolContext, tool_error_handler

logger = logging.getLogger("tool_messaging")


class SendMessageInput(BaseModel):
    """Input schema for send_message_to_user tool."""
    message: str = Field(description="The message text to send to the user")
    message_type: Literal["info", "success", "warning", "error", "planning", "execution"] = Field(
        default="info",
        description="Type of message for styling: info, success, warning, error, planning, execution"
    )


class PlanConfirmationInput(BaseModel):
    """Input schema for present_plan_for_confirmation tool."""
    plan_id: str = Field(description="Unique identifier for this plan (mission_id from create_waypoint_sequence)")
    plan_summary: str = Field(description="Brief summary of the mission plan")
    estimated_time: float = Field(
        description="Estimated duration of the mission in seconds. Calculate as: waypoint_count x 20-30 seconds per waypoint"
    )


@tool_error_handler
async def _send_message_impl(
    message: str,
    message_type: str,
    context: ToolContext
) -> ToolResult:
    """Internal implementation of send_message_to_user."""
    
    websocket = context.websocket
    
    if not websocket:
        logger.warning(f"No WebSocket connection, message not sent: {message}")
        return ToolResult.ok({
            "sent": False,
            "reason": "No WebSocket connection",
            "message": message
        })
    
    # Create message payload
    payload = {
        "type": "ai_message",
        "role": "assistant",
        "content": message,
        "message_type": message_type,
        "timestamp": datetime.now().isoformat(),
        "session_id": context.session_id,
    }
    
    try:
        await websocket.send_json(payload)
        
        return ToolResult.ok({
            "sent": True,
            "message": message,
            "message_type": message_type
        })
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        return ToolResult.fail(f"Failed to send message: {e}")


@tool_error_handler
async def _present_plan_impl(
    plan_id: str,
    plan_summary: str,
    estimated_time: float,
    context: ToolContext
) -> ToolResult:
    """Internal implementation of present_plan_for_confirmation."""
    
    websocket = context.websocket
    mission_executor = context.mission_executor
    
    if not websocket:
        logger.warning("No WebSocket connection, plan confirmation not sent")
        return ToolResult.ok({
            "presented": False,
            "reason": "No WebSocket connection",
            "plan_id": plan_id
        })
    
    # Retrieve waypoints from mission executor
    if not mission_executor:
        logger.error("No mission executor available")
        return ToolResult.fail("Mission executor not available")
    
    waypoints = mission_executor.get_waypoints(plan_id)
    if not waypoints:
        logger.error(f"No waypoints found for plan_id: {plan_id}")
        return ToolResult.fail(f"No waypoints found for plan_id: {plan_id}. Make sure to call create_waypoint_sequence first.")
    
    # Store the estimated_time with the mission for progress calculations
    mission_executor.set_estimated_time(plan_id, estimated_time)
    
    # Format waypoints for display
    display_waypoints = []
    for i, wp in enumerate(waypoints):
        display_wp = {
            "id": wp.get("waypoint_id", f"wp_{i}"),
            "description": wp.get("description", f"Waypoint {i}"),
            "action": wp.get("action", "move_to"),
        }
        
        # Include position if available
        if wp.get("target_lat") and wp.get("target_lon"):
            display_wp["position"] = {
                "lat": wp.get("target_lat"),
                "lon": wp.get("target_lon"),
                "alt": wp.get("target_altitude")
            }
        
        display_waypoints.append(display_wp)
    
    # Create confirmation payload
    payload = {
        "type": "plan_confirmation",
        "role": "assistant",
        "content": plan_summary,
        "timestamp": datetime.now().isoformat(),
        "session_id": context.session_id,
        "plan_data": {
            "plan_id": plan_id,
            "summary": plan_summary,
            "waypoints": display_waypoints,
            "waypoint_count": len(display_waypoints),
            "estimated_duration": estimated_time,
        }
    }
    
    try:
        await websocket.send_json(payload)
        logger.info(f"Presented plan for confirmation: {plan_id} with {len(display_waypoints)} waypoints")
        
        return ToolResult.ok({
            "presented": True,
            "plan_id": plan_id,
            "waypoint_count": len(display_waypoints),
            "status": "awaiting_confirmation",
            "message": "Plan presented to user. Waiting for confirmation or abort."
        })
    except Exception as e:
        logger.error(f"Failed to present plan: {e}")
        return ToolResult.fail(f"Failed to present plan: {e}")


def create_messaging_tools(context: ToolContext):
    """
    Create messaging tools bound to a specific context.
    Returns tuple of (send_message_to_user, present_plan_for_confirmation).
    """
    
    @tool(args_schema=SendMessageInput)
    async def send_message_to_user_bound(
        message: str,
        message_type: Literal["info", "success", "warning", "error", "planning", "execution"] = "info"
    ) -> str:
        """
        Send a message to the user through the WebSocket chat interface.
        
        Use this tool to:
        - Acknowledge user commands before planning
        - Provide status updates during execution
        - Report warnings or errors
        - Ask clarifying questions
        
        Message types: info, success, warning, error, planning, execution
        
        Args:
            message: The message text to display
            message_type: Type of message for styling
            
        Returns:
            JSON string confirming message was sent
        """
        result = await _send_message_impl(message, message_type, context)
        if result.success:
            return json.dumps(result.data)
        else:
            return json.dumps({"error": result.error})
    
    @tool(args_schema=PlanConfirmationInput)
    async def present_plan_for_confirmation_bound(
        plan_id: str,
        plan_summary: str,
        estimated_time: float
    ) -> str:
        """
        Present a mission plan to the user for confirmation (human-in-the-loop).
        
        IMPORTANT: Always call this after create_waypoint_sequence!
        The system will pause and wait for user confirmation before execution.
        
        Waypoints are automatically retrieved from the mission executor using the plan_id.
        
        Args:
            plan_id: The mission_id returned from create_waypoint_sequence
            plan_summary: Brief summary of the mission plan (e.g., "Hexagon path with 6 corners")
            estimated_time: REQUIRED. Estimated duration in seconds. Calculate as: waypoint_count × 20-30 sec
            
        Returns:
            JSON string with status indicating plan was presented, awaiting confirmation
        """
        result = await _present_plan_impl(plan_id, plan_summary, estimated_time, context)
        if result.success:
            return json.dumps(result.data)
        else:
            return json.dumps({"error": result.error})
    
    return send_message_to_user_bound, present_plan_for_confirmation_bound
