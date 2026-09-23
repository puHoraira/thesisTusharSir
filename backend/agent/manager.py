"""
Agent Manager for Serial Communication
Manages DroneAgent instances for serial-based sessions
"""

import logging
from typing import Dict, Optional
from agent.agent import DroneAgent
from services import get_drone_service, get_telemetry_service

logger = logging.getLogger("agent_manager")


class AgentManager:
    """
    Manages DroneAgent instances for serial communication.
    
    For serial communication, we use a single agent instance per backend
    (unlike WebSocket where each connection has its own agent).
    """
    
    def __init__(self):
        self.agent: Optional[DroneAgent] = None
        self.session_id = "serial_session"
    
    def get_or_create_agent(self) -> DroneAgent:
        """
        Get existing agent or create new one for serial communication.
        
        Returns:
            DroneAgent instance
        """
        if self.agent is None:
            logger.info("Creating new DroneAgent for serial communication")
            
            drone_service = get_drone_service()
            telemetry_service = get_telemetry_service()
            
            self.agent = DroneAgent(
                session_id=self.session_id,
                websocket=None,  # No WebSocket for serial
                drone_service=drone_service,
                telemetry_service=telemetry_service,
            )
            
            logger.info(f"DroneAgent created for serial session: {self.session_id}")
        
        return self.agent
    
    async def clear_history(self) -> None:
        """Clear conversation history"""
        if self.agent:
            # Reset state messages
            self.agent.state["messages"] = []
            logger.info("Agent conversation history cleared")
    
    def get_state(self) -> Dict:
        """Get current agent state"""
        if self.agent:
            return self.agent.state
        return {}


# Global singleton
_agent_manager: Optional[AgentManager] = None


def get_agent_manager() -> AgentManager:
    """Get the global agent manager for serial communication"""
    global _agent_manager
    if _agent_manager is None:
        _agent_manager = AgentManager()
    return _agent_manager
