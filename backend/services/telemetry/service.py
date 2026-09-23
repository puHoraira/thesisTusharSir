"""
Main telemetry service class that orchestrates all telemetry operations.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Callable, Awaitable

if TYPE_CHECKING:
    from services.drone import DroneService

from config import TELEMETRY_STREAM_MAX_RESTARTS, TELEMETRY_STREAM_RESTART_BACKOFF
from .connection import ConnectionManager
from .broadcaster import Broadcaster
from .streams import TelemetryStreams

logger = logging.getLogger("telemetry_service")


class TelemetryService:
    """Service for streaming telemetry data to WebSocket clients"""
    
    def __init__(self, drone_service: "DroneService", shutdown_event=None):
        """
        Initialize telemetry service
        
        Args:
            drone_service: DroneService instance for accessing drone telemetry
            shutdown_event: Optional asyncio.Event for graceful shutdown
        """
        self.drone_service = drone_service
        
        # Initialize component managers
        self.connection_manager = ConnectionManager(shutdown_event)
        self.broadcaster = Broadcaster(self.connection_manager.connections)
        self.streams = TelemetryStreams(drone_service, self.connection_manager, self.broadcaster)
        
        self.streaming_tasks = []
    
    async def _stream_with_restart(
        self, 
        stream_name: str, 
        stream_func: Callable[[], Awaitable[None]]
    ):
        """
        Wrapper that runs a stream function with auto-restart on failure.
        
        Args:
            stream_name: Name of the stream for logging and health tracking
            stream_func: Async function that runs the stream
        """
        while not self.connection_manager.is_shutdown_requested():
            try:
                self.streams._update_stream_health(stream_name)
                await stream_func()
                
                # Stream exited normally (shutdown or disconnection)
                if self.connection_manager.is_shutdown_requested():
                    logger.debug(f"{stream_name} stream stopped normally")
                    break
                    
            except asyncio.CancelledError:
                logger.debug(f"{stream_name} stream cancelled")
                raise
            except Exception as e:
                error_str = str(e)
                is_expected_close = (
                    self.connection_manager.is_shutdown_requested() or 
                    "Socket closed" in error_str or 
                    "UNAVAILABLE" in error_str
                )
                
                if is_expected_close:
                    logger.debug(f"{stream_name} stream closed (shutdown or connection lost)")
                    break
                
                # Unexpected error - attempt restart if under limit
                if self.streams._should_restart_stream(stream_name):
                    self.streams._record_stream_restart(stream_name)
                    restart_count = self.streams._stream_restart_counts.get(stream_name, 0)
                    
                    # Calculate exponential backoff
                    backoff = TELEMETRY_STREAM_RESTART_BACKOFF * (2 ** (restart_count - 1))
                    backoff = min(backoff, 30.0)  # Cap at 30 seconds
                    
                    logger.warning(
                        f"{stream_name} stream failed: {e}. "
                        f"Restarting ({restart_count}/{TELEMETRY_STREAM_MAX_RESTARTS}) in {backoff:.1f}s"
                    )
                    
                    await asyncio.sleep(backoff)
                    
                    # Check if still connected and not shutting down
                    if not self.drone_service.connected or self.connection_manager.is_shutdown_requested():
                        logger.debug(f"{stream_name} stream not restarting - disconnected or shutdown")
                        break
                else:
                    logger.error(
                        f"{stream_name} stream failed: {e}. "
                        f"Max restarts ({TELEMETRY_STREAM_MAX_RESTARTS}) exceeded - not restarting"
                    )
                    break
    
    @property
    def shutdown_event(self):
        """Get shutdown event"""
        return self.connection_manager.shutdown_event
    
    @shutdown_event.setter
    def shutdown_event(self, value):
        """Set shutdown event"""
        self.connection_manager.shutdown_event = value
    
    async def add_connection(self, websocket):
        """Add a new WebSocket connection"""
        await self.connection_manager.add_connection(websocket)
    
    async def remove_connection(self, websocket):
        """Remove a WebSocket connection"""
        await self.connection_manager.remove_connection(websocket)
    
    def request_shutdown(self):
        """Request graceful shutdown of all streaming tasks"""
        self.connection_manager.request_shutdown()
    
    @property
    def connections(self):
        """Get connections set"""
        return self.connection_manager.connections
    
    async def stream_position(self):
        """Stream position telemetry using position and altitude streams"""
        await self.streams.stream_position()
    
    async def stream_battery(self):
        """Stream battery telemetry"""
        await self.streams.stream_battery()
    
    async def stream_flight_mode(self):
        """Stream flight mode telemetry"""
        await self.streams.stream_flight_mode()
    
    async def stream_armed_status(self):
        """Stream armed status telemetry"""
        await self.streams.stream_armed_status()
    
    async def start_streaming(self):
        """Start all telemetry streaming tasks with auto-restart"""
        if not self.drone_service.connected:
            return
        
        # Reset stream health before starting
        self.streams._stream_restart_counts = {k: 0 for k in self.streams._stream_restart_counts}
        
        # DISABLE telemetry rate setting - it overwhelms Pixhawk and causes "link 1 down"
        # Use Pixhawk default rates instead
        # await self.streams.set_telemetry_rates()
        logger.info("Using Pixhawk default telemetry rates (rate setting disabled to prevent MAVProxy crashes)")
        
        # Start the broadcaster (separate task that reads latest values and sends to clients)
        self.streams.start_broadcaster()
        
        try:
            # Stagger stream starts to prevent subscription burst
            # Each subscription creates initial data that can overwhelm the queue
            
            position_task = asyncio.create_task(
                self._stream_with_restart("position", self.stream_position)
            )
            await asyncio.sleep(0.2)  # Small delay between subscriptions
            
            battery_task = asyncio.create_task(
                self._stream_with_restart("battery", self.stream_battery)
            )
            await asyncio.sleep(0.2)
            
            flight_mode_task = asyncio.create_task(
                self._stream_with_restart("flight_mode", self.stream_flight_mode)
            )
            await asyncio.sleep(0.2)
            
            armed_task = asyncio.create_task(
                self._stream_with_restart("armed_status", self.stream_armed_status)
            )
            
            self.streaming_tasks = [position_task, battery_task, flight_mode_task, armed_task]
            
            logger.debug("Telemetry streams started")
            await asyncio.gather(*self.streaming_tasks, return_exceptions=True)
        except asyncio.CancelledError:
            # Stop broadcaster and cancel all streaming tasks
            self.streams.stop_broadcaster()
            for task in self.streaming_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self.streaming_tasks, return_exceptions=True)
            raise
        except Exception as e:
            logger.error(f"Telemetry stream error: {e}")
        finally:
            # Ensure broadcaster is stopped
            self.streams.stop_broadcaster()
    
    def get_stream_health(self):
        """Get health status for all telemetry streams."""
        return self.streams.get_stream_health()
