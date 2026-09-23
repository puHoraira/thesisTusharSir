"""
Broadcasting functionality for telemetry service.
"""

import logging
from typing import Set

from fastapi import WebSocket

logger = logging.getLogger("telemetry_broadcaster")


class Broadcaster:
    """Handles broadcasting telemetry data to WebSocket clients."""
    
    def __init__(self, connections: Set[WebSocket]):
        self.connections = connections
    
    async def broadcast(self, data: dict):
        """Broadcast data to all connected clients"""
        disconnected = set()
        for connection in self.connections:
            try:
                await connection.send_json(data)
            except:
                disconnected.add(connection)
        
        self.connections.difference_update(disconnected)
