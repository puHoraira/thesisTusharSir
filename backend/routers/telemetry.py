"""
Telemetry router for WebSocket telemetry streaming.
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services import get_telemetry_service

logger = logging.getLogger("telemetry_router")

router = APIRouter()


@router.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    """WebSocket endpoint for streaming telemetry data"""
    telemetry_service = get_telemetry_service()
    
    await telemetry_service.add_connection(websocket)
    
    try:
        # Keep connection alive and let streaming tasks handle data
        while True:
            await websocket.receive_text()  # Just keep connection alive
    except WebSocketDisconnect as e:
        logger.info(f"Telemetry client disconnected (code={e.code})")
    finally:
        await telemetry_service.remove_connection(websocket)
