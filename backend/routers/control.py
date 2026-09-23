"""
Control router for WebSocket control commands.
"""

import json
import logging
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services import get_drone_service
from schemas import ControlCommand

logger = logging.getLogger("control_router")

router = APIRouter()

# WebSocket connection set
control_connections: Set[WebSocket] = set()

# Track velocity command count per connection for periodic success responses
velocity_command_counts: dict = {}


@router.websocket("/ws/control")
async def websocket_control(websocket: WebSocket):
    """WebSocket endpoint for receiving control commands"""
    drone_service = get_drone_service()
    
    await websocket.accept()
    control_connections.add(websocket)
    logger.debug(f"Control client connected. Total: {len(control_connections)}")
    
    try:
        while True:
            data = await websocket.receive_text()
            
            try:
                command = json.loads(data)
                cmd = ControlCommand(**command)
                
                # Process command (log important commands only)
                if cmd.type == "takeoff":
                    logger.info(f"Takeoff command: altitude={cmd.altitude}m")
                    success, message = await drone_service.takeoff(cmd.altitude)
                    response = {
                        "status": "success" if success else "error",
                        "message": message
                    }
                    if not success:
                        logger.warning(f"Takeoff failed: {message}")
                    await websocket.send_json(response)
                
                elif cmd.type == "land":
                    logger.info("Land command received")
                    success, message = await drone_service.land()
                    response = {
                        "status": "success" if success else "error",
                        "message": message
                    }
                    if not success:
                        logger.warning(f"Land failed: {message}")
                    await websocket.send_json(response)
                
                elif cmd.type == "emergency_stop":
                    logger.warning("EMERGENCY_STOP command received")
                    success, message = await drone_service.emergency_stop()
                    response = {
                        "status": "success" if success else "error",
                        "message": message
                    }
                    if not success:
                        logger.error(f"Emergency stop failed: {message}")
                    await websocket.send_json(response)
                
                elif cmd.type == "velocity_body":
                    if not drone_service.connected:
                        response = {"status": "error", "message": "Drone not connected"}
                        logger.warning(f"Velocity command rejected: {response['message']}")
                        await websocket.send_json(response)
                        continue
                    
                    # Scale velocities from normalized (-1 to 1) to actual m/s
                    limits = drone_service.get_velocity_limits()
                    vx = drone_service.scale_velocity(cmd.vx, limits["horizontal"])
                    vy = drone_service.scale_velocity(cmd.vy, limits["horizontal"])
                    vz = drone_service.scale_velocity(cmd.vz, limits["vertical"])
                    yaw_rate = drone_service.scale_velocity(cmd.yaw_rate, limits["yaw_rate"])
                    
                    # Update velocity state (non-blocking, backend streamer handles actual sending)
                    success, message = await drone_service.set_velocity_body(vx, vy, vz, yaw_rate)
                    
                    # Send response immediately (only log errors to reduce verbosity)
                    response = {
                        "status": "success" if success else "error",
                        "message": message
                    }
                    if not success:
                        logger.warning(f"Velocity command rejected: {message}")
                    await websocket.send_json(response)
                
                else:
                    response = {"status": "error", "message": f"Unknown command type: {cmd.type}"}
                    logger.warning(f"{response['message']}")
                    await websocket.send_json(response)
                    
            except Exception as e:
                logger.error(f"Command processing error: {e}")
                await websocket.send_json({"status": "error", "message": str(e)})
                
    except WebSocketDisconnect as e:
        logger.info(f"Control client disconnected (code={e.code})")
    finally:
        control_connections.discard(websocket)
        # Clean up velocity command counter
        connection_id = id(websocket)
        velocity_command_counts.pop(connection_id, None)
        logger.debug(f"Control client removed. Total: {len(control_connections)}")
