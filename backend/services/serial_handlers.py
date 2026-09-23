"""
Message handlers for serial communication
Maps serial messages to backend actions
"""

import logging
from typing import Dict, Any
from services.serial_communication import SerialCommunicationManager
from services import get_drone_service

logger = logging.getLogger("serial_handlers")


async def handle_agent_command(
    message: Dict[str, Any],
    serial_comm: SerialCommunicationManager,
) -> None:
    """
    Handle AI agent command from mobile

    Message format:
    {
        "type": "agent_command",
        "id": "uuid",
        "timestamp": "2024-01-01T00:00:00Z",
        "data": {
            "content": "Take off to 10 meters"
        }
    }
    """
    try:
        message_id = message.get("id")
        content = message.get("data", {}).get("content")

        if not content:
            await serial_comm.send_error(message_id, "Missing content", "INVALID_DATA")
            return

        logger.info(f"Agent command: {content}")

        # Import agent manager here to avoid circular imports
        from agent.manager import get_agent_manager

        agent_manager = get_agent_manager()
        agent = agent_manager.get_or_create_agent()

        # Process command with agent
        # Agent will send responses via serial_comm directly
        await agent.process_command_serial(content, message_id, serial_comm)

    except Exception as e:
        logger.error(f"Error handling agent command: {e}", exc_info=True)
        await serial_comm.send_error(
            message.get("id"), f"Failed to process command: {str(e)}", "AGENT_ERROR"
        )


async def handle_control_command(
    message: Dict[str, Any],
    serial_comm: SerialCommunicationManager,
) -> None:
    """
    Handle direct control command (takeoff, land, velocity, etc.)

    Message format:
    {
        "type": "control_command",
        "id": "uuid",
        "data": {
            "action": "takeoff",
            "altitude": 10.0
        }
    }
    """
    try:
        message_id = message.get("id")
        data = message.get("data", {})
        action = data.get("action")

        if not action:
            await serial_comm.send_error(message_id, "Missing action", "INVALID_DATA")
            return

        logger.info(f"Control command: {action}")

        drone_service = get_drone_service()

        # Execute action
        if action == "takeoff":
            altitude = data.get("altitude", 2.5)
            await drone_service.takeoff(altitude)
            await serial_comm.send_response(
                message_id,
                "command_result",
                {"status": "success", "message": f"Taking off to {altitude}m"},
            )

        elif action == "land":
            await drone_service.land()
            await serial_comm.send_response(
                message_id,
                "command_result",
                {"status": "success", "message": "Landing"},
            )

        elif action == "emergency_stop":
            await drone_service.emergency_stop()
            await serial_comm.send_response(
                message_id,
                "command_result",
                {"status": "success", "message": "Emergency stop executed"},
            )

        elif action == "velocity_body":
            vx = data.get("vx", 0.0)
            vy = data.get("vy", 0.0)
            vz = data.get("vz", 0.0)
            yaw_rate = data.get("yaw_rate", 0.0)

            await drone_service.send_velocity_body(vx, vy, vz, yaw_rate)
            await serial_comm.send_response(
                message_id,
                "command_result",
                {"status": "success", "message": "Velocity command sent"},
            )

        elif action == "move_ned":
            north = data.get("north", 0.0)
            east = data.get("east", 0.0)
            down = data.get("down", 0.0)

            await drone_service.move_ned(north, east, down)
            await serial_comm.send_response(
                message_id,
                "command_result",
                {"status": "success", "message": "Move command sent"},
            )

        else:
            await serial_comm.send_error(
                message_id, f"Unknown action: {action}", "UNKNOWN_ACTION"
            )

    except Exception as e:
        logger.error(f"Error handling control command: {e}", exc_info=True)
        await serial_comm.send_error(
            message.get("id"), f"Failed to execute command: {str(e)}", "CONTROL_ERROR"
        )


async def handle_plan_confirm(
    message: Dict[str, Any],
    serial_comm: SerialCommunicationManager,
) -> None:
    """
    Handle plan confirmation from mobile

    Message format:
    {
        "type": "plan_confirm",
        "id": "uuid",
        "data": {
            "plan_id": "plan-uuid"
        }
    }
    """
    try:
        message_id = message.get("id")
        plan_id = message.get("data", {}).get("plan_id")

        if not plan_id:
            await serial_comm.send_error(message_id, "Missing plan_id", "INVALID_DATA")
            return

        logger.info(f"Plan confirmed: {plan_id}")

        # Import agent manager
        from agent.manager import get_agent_manager

        agent_manager = get_agent_manager()
        agent = agent_manager.get_or_create_agent()

        # Confirm plan
        await agent.confirm_plan_serial(plan_id, message_id, serial_comm)

    except Exception as e:
        logger.error(f"Error handling plan confirm: {e}", exc_info=True)
        await serial_comm.send_error(
            message.get("id"), f"Failed to confirm plan: {str(e)}", "PLAN_ERROR"
        )


async def handle_plan_abort(
    message: Dict[str, Any],
    serial_comm: SerialCommunicationManager,
) -> None:
    """
    Handle plan abort from mobile

    Message format:
    {
        "type": "plan_abort",
        "id": "uuid",
        "data": {
            "plan_id": "plan-uuid",
            "feedback": "Optional feedback"
        }
    }
    """
    try:
        message_id = message.get("id")
        data = message.get("data", {})
        plan_id = data.get("plan_id")
        feedback = data.get("feedback")

        if not plan_id:
            await serial_comm.send_error(message_id, "Missing plan_id", "INVALID_DATA")
            return

        logger.info(f"Plan aborted: {plan_id}, feedback: {feedback}")

        # Import agent manager
        from agent.manager import get_agent_manager

        agent_manager = get_agent_manager()
        agent = agent_manager.get_or_create_agent()

        # Abort plan
        await agent.abort_plan_serial(plan_id, feedback, message_id, serial_comm)

    except Exception as e:
        logger.error(f"Error handling plan abort: {e}", exc_info=True)
        await serial_comm.send_error(
            message.get("id"), f"Failed to abort plan: {str(e)}", "PLAN_ERROR"
        )


async def handle_status_request(
    message: Dict[str, Any],
    serial_comm: SerialCommunicationManager,
) -> None:
    """
    Handle status request from mobile

    Message format:
    {
        "type": "status_request",
        "id": "uuid"
    }
    """
    try:
        message_id = message.get("id")

        logger.debug("Status request received")

        drone_service = get_drone_service()

        # Gather status from telemetry
        from services import get_telemetry_service
        telemetry_service = get_telemetry_service()
        
        status = {
            "connected": drone_service.connected,
            "armed": telemetry_service.streams._latest_armed if telemetry_service.streams._latest_armed is not None else False,
            "flight_mode": telemetry_service.streams._latest_flight_mode.name if telemetry_service.streams._latest_flight_mode else "UNKNOWN",
            "offboard_active": drone_service.offboard_active,
        }

        await serial_comm.send_response(
            message_id,
            "status_response",
            status,
        )

    except Exception as e:
        logger.error(f"Error handling status request: {e}", exc_info=True)
        await serial_comm.send_error(
            message.get("id"), f"Failed to get status: {str(e)}", "STATUS_ERROR"
        )


async def handle_clear_history(
    message: Dict[str, Any],
    serial_comm: SerialCommunicationManager,
) -> None:
    """
    Handle clear history request

    Message format:
    {
        "type": "clear_history",
        "id": "uuid"
    }
    """
    try:
        message_id = message.get("id")

        logger.info("Clear history request")

        # Import agent manager
        from agent.manager import get_agent_manager

        agent_manager = get_agent_manager()

        # Clear history
        await agent_manager.clear_history()

        await serial_comm.send_response(
            message_id,
            "command_result",
            {"status": "success", "message": "History cleared"},
        )

    except Exception as e:
        logger.error(f"Error handling clear history: {e}", exc_info=True)
        await serial_comm.send_error(
            message.get("id"), f"Failed to clear history: {str(e)}", "HISTORY_ERROR"
        )


def register_all_handlers(serial_comm: SerialCommunicationManager) -> None:
    """
    Register all message handlers with serial communication manager

    Args:
        serial_comm: Serial communication manager
    """
    # Create wrapper functions that pass serial_comm to handlers
    async def wrapped_agent_command(msg):
        await handle_agent_command(msg, serial_comm)

    async def wrapped_control_command(msg):
        await handle_control_command(msg, serial_comm)

    async def wrapped_plan_confirm(msg):
        await handle_plan_confirm(msg, serial_comm)

    async def wrapped_plan_abort(msg):
        await handle_plan_abort(msg, serial_comm)

    async def wrapped_status_request(msg):
        await handle_status_request(msg, serial_comm)

    async def wrapped_clear_history(msg):
        await handle_clear_history(msg, serial_comm)

    # Register handlers
    serial_comm.register_handler("agent_command", wrapped_agent_command)
    serial_comm.register_handler("control_command", wrapped_control_command)
    serial_comm.register_handler("plan_confirm", wrapped_plan_confirm)
    serial_comm.register_handler("plan_abort", wrapped_plan_abort)
    serial_comm.register_handler("status_request", wrapped_status_request)
    serial_comm.register_handler("clear_history", wrapped_clear_history)

    logger.info("All serial message handlers registered")