"""
FastAPI router for LLM Agent WebSocket communication.
Handles bidirectional chat between frontend and drone agent.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from services import get_drone_service, get_telemetry_service
from agent.agent import DroneAgent, create_agent

logger = logging.getLogger("agent_router")

router = APIRouter(prefix="/ws/agent", tags=["agent"])

# Store active agent sessions (websocket -> agent)
_active_sessions: Dict[WebSocket, DroneAgent] = {}


class WebSocketMessage(BaseModel):
    """Base WebSocket message schema."""
    type: str
    timestamp: Optional[str] = None


class UserCommandMessage(WebSocketMessage):
    """User command message."""
    type: str = "user_command"
    content: str


class PlanConfirmationMessage(WebSocketMessage):
    """Plan confirmation/abort message."""
    type: str  # "plan_confirm" or "plan_abort"
    plan_id: str
    feedback: Optional[str] = None


class EmergencyStopMessage(WebSocketMessage):
    """Emergency stop message."""
    type: str = "emergency_stop"


@router.websocket("/chat")
async def llm_chat_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for LLM agent chat.
    
    Handles:
    - User natural language commands
    - Plan confirmation/abort responses
    - Emergency stop requests
    - Agent status updates
    
    Message Types (Client -> Server):
    - user_command: Natural language command from user
    - plan_confirm: User confirms a pending plan
    - plan_abort: User aborts a pending plan
    - emergency_stop: Emergency stop request
    - clear_history: Clear conversation history and start fresh
    
    Message Types (Server -> Client):
    - ai_message: LLM response message
    - plan_confirmation: Plan presented for confirmation
    - status_update: Mission/agent status update
    - error: Error message
    """
    await websocket.accept()
    logger.info("New WebSocket connection for LLM chat")
    
    # Create agent for this session
    drone_service = get_drone_service()
    telemetry_service = get_telemetry_service()
    
    agent = create_agent(
        websocket=websocket,
        drone_service=drone_service,
        telemetry_service=telemetry_service,
    )
    
    _active_sessions[websocket] = agent
    
    # Send welcome message
    await websocket.send_json({
        "type": "ai_message",
        "role": "assistant",
        "content": "Hello! I'm your drone control assistant. How can I help you today? You can ask me to fly the drone, create mission plans, or check the drone's status.",
        "message_type": "info",
        "timestamp": datetime.now().isoformat(),
        "session_id": agent.session_id,
    })
    
    try:
        while True:
            # Receive message from client
            data = await websocket.receive_json()
            
            message_type = data.get("type", "")
            logger.debug(f"Received message type: {message_type}")
            
            try:
                if message_type == "user_command":
                    # Process natural language command
                    content = data.get("content", "")
                    if not content.strip():
                        await _send_error(websocket, "Empty command received", agent.session_id)
                        continue
                    
                    # Show typing indicator
                    await websocket.send_json({
                        "type": "status_update",
                        "status": "processing",
                        "message": "Processing your command...",
                        "timestamp": datetime.now().isoformat(),
                        "session_id": agent.session_id,
                    })
                    
                    # Process command
                    result = await agent.process_command(content)
                    
                    # Handle result
                    if result.get("status") == "error":
                        await _send_error(websocket, result.get("error", "Unknown error"), agent.session_id)
                    elif result.get("status") == "awaiting_confirmation":
                        # Plan confirmation is sent by the agent via messaging tool
                        logger.info("Command resulted in plan awaiting confirmation")
                    else:
                        logger.info(f"Command processed: {result.get('status')}")
                
                elif message_type == "plan_confirm":
                    # User confirms plan
                    plan_id = data.get("plan_id", "")
                    if not plan_id:
                        await _send_error(websocket, "plan_id is required", agent.session_id)
                        continue
                    
                    result = await agent.handle_plan_confirmation(
                        plan_id=plan_id,
                        confirmed=True,
                        feedback=None
                    )
                    
                    if result.get("status") == "error":
                        await _send_error(websocket, result.get("error", "Unknown error"), agent.session_id)
                
                elif message_type == "plan_abort":
                    # User aborts plan
                    plan_id = data.get("plan_id", "")
                    feedback = data.get("feedback", "")
                    
                    if not plan_id:
                        await _send_error(websocket, "plan_id is required", agent.session_id)
                        continue
                    
                    result = await agent.handle_plan_confirmation(
                        plan_id=plan_id,
                        confirmed=False,
                        feedback=feedback
                    )
                    
                    if result.get("status") == "error":
                        await _send_error(websocket, result.get("error", "Unknown error"), agent.session_id)
                
                elif message_type == "emergency_stop":
                    # Emergency stop
                    result = await agent.emergency_stop()
                    
                    await websocket.send_json({
                        "type": "status_update",
                        "status": "emergency_stop",
                        "success": result.get("success", False),
                        "message": result.get("message", "Emergency stop executed"),
                        "timestamp": datetime.now().isoformat(),
                        "session_id": agent.session_id,
                    })
                
                elif message_type == "clear_history":
                    # Clear conversation history
                    result = await agent.clear_conversation_history()
                    
                    await websocket.send_json({
                        "type": "status_update",
                        "status": "history_cleared",
                        "message": result.get("message", "Conversation history cleared"),
                        "timestamp": datetime.now().isoformat(),
                        "session_id": agent.session_id,
                    })
                
                elif message_type == "get_status":
                    # Get current agent state
                    state = agent.get_state()
                    
                    await websocket.send_json({
                        "type": "status_update",
                        "status": "current_state",
                        "data": {
                            "current_state": state.get("current_state"),
                            "mission_active": state.get("mission_active"),
                            "awaiting_confirmation": state.get("awaiting_plan_confirmation"),
                            "error_state": state.get("error_state"),
                        },
                        "timestamp": datetime.now().isoformat(),
                        "session_id": agent.session_id,
                    })
                
                else:
                    logger.warning(f"Unknown message type: {message_type}")
                    await _send_error(
                        websocket, 
                        f"Unknown message type: {message_type}. Valid types: user_command, plan_confirm, plan_abort, emergency_stop, get_status",
                        agent.session_id
                    )
            
            except Exception as e:
                logger.error(f"Error processing message: {e}", exc_info=True)
                await _send_error(websocket, f"Error processing message: {e}", agent.session_id)
    
    except WebSocketDisconnect as e:
        logger.info(
            f"WebSocket disconnected for session {agent.session_id} (code={e.code})"
        )
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
    finally:
        # Cleanup
        if websocket in _active_sessions:
            agent = _active_sessions.pop(websocket)
            await agent.cleanup()
        logger.info("WebSocket connection closed")


async def _send_error(websocket: WebSocket, error: str, session_id: str):
    """Send error message to client."""
    try:
        await websocket.send_json({
            "type": "error",
            "error": error,
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
        })
    except Exception as e:
        logger.error(f"Failed to send error message: {e}")


def get_active_sessions() -> Dict[str, Dict[str, Any]]:
    """Get information about active agent sessions."""
    sessions = {}
    for ws, agent in _active_sessions.items():
        sessions[agent.session_id] = {
            "session_id": agent.session_id,
            "mission_active": agent.is_mission_active(),
            "awaiting_confirmation": agent.is_awaiting_confirmation(),
            "current_state": agent.state.get("current_state"),
        }
    return sessions
