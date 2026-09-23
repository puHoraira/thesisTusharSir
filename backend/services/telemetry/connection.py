"""
Connection management for telemetry service.
"""

import logging
from typing import Set

from fastapi import WebSocket

logger = logging.getLogger("telemetry_connection_manager")


class ConnectionManager:
    """Manages WebSocket connections for telemetry service."""
    
    def __init__(self, shutdown_event=None):
        self.connections: Set[WebSocket] = set()
        self.shutdown_event = shutdown_event
        self._shutdown_requested = False
    
    async def add_connection(self, websocket: WebSocket):
        """Add a new WebSocket connection"""
        await websocket.accept()
        self.connections.add(websocket)
        logger.info(f"Telemetry client connected (total={len(self.connections)})")
    
    async def remove_connection(self, websocket: WebSocket):
        """Remove a WebSocket connection"""
        self.connections.discard(websocket)
        logger.info(f"Telemetry client removed (total={len(self.connections)})")
    
    def request_shutdown(self):
        """Request graceful shutdown of all streaming tasks"""
        self._shutdown_requested = True
        logger.info("Telemetry service shutdown requested")
    
    def is_shutdown_requested(self) -> bool:
        """Check if shutdown has been requested"""
        if self._shutdown_requested:
            return True
        if self.shutdown_event and self.shutdown_event.is_set():
            return True
        return False
