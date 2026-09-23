"""
Telemetry streaming methods for different data types.
Includes auto-restart with error logging (no fallback actions).

Uses producer-consumer pattern to prevent MAVSDK callback queue overflow:
- Producers: Fast consumers that only store latest values (no blocking)
- Consumers: Separate broadcaster task that reads latest values on a timer
"""

import asyncio
import logging
import math
import time
from datetime import datetime
from typing import Optional, TYPE_CHECKING, Dict, Any

if TYPE_CHECKING:
    from services.drone import DroneService

from .utils import sanitize_for_json
from config import (
    TELEMETRY_STREAM_MAX_RESTARTS,
    TELEMETRY_STREAM_RESTART_BACKOFF,
    TELEMETRY_STALE_THRESHOLD
)

logger = logging.getLogger("telemetry_streams")


class TelemetryStreams:
    """Manages all telemetry streaming operations with auto-restart and health monitoring."""
    
    def __init__(self, drone_service: "DroneService", connection_manager, broadcaster):
        self.drone_service = drone_service
        self.connection_manager = connection_manager
        self.broadcaster = broadcaster
        
        # Latest telemetry values (updated by fast consumers, read by broadcaster)
        # Note: position includes altitude, so we don't need a separate _latest_altitude
        self._latest_position: Optional[Any] = None
        self._latest_velocity: Optional[Any] = None
        self._latest_heading: Optional[float] = None  # Yaw/heading in degrees
        self._latest_battery: Optional[Any] = None
        self._latest_flight_mode: Optional[Any] = None
        self._latest_armed: Optional[bool] = None
        
        # Broadcaster task
        self._broadcaster_task: Optional[asyncio.Task] = None
        
        # Stream health tracking
        self._stream_restart_counts: Dict[str, int] = {
            "position": 0,
            "battery": 0,
            "flight_mode": 0,
            "armed_status": 0
        }
        self._stream_last_update: Dict[str, Optional[float]] = {
            "position": None,
            "battery": None,
            "flight_mode": None,
            "armed_status": None
        }
        self._stream_last_restart: Dict[str, Optional[float]] = {
            "position": None,
            "battery": None,
            "flight_mode": None,
            "armed_status": None
        }
    
    def _update_stream_health(self, stream_name: str):
        """Update last update time for stream health tracking."""
        self._stream_last_update[stream_name] = time.time()
        
        # Reset restart count if stream has been healthy for a while
        if self._stream_last_restart.get(stream_name):
            if time.time() - self._stream_last_restart[stream_name] > 60.0:
                self._stream_restart_counts[stream_name] = 0
    
    def _should_restart_stream(self, stream_name: str) -> bool:
        """Check if stream should be restarted (under max restarts)."""
        return self._stream_restart_counts.get(stream_name, 0) < TELEMETRY_STREAM_MAX_RESTARTS
    
    def _record_stream_restart(self, stream_name: str):
        """Record a stream restart attempt."""
        self._stream_restart_counts[stream_name] = self._stream_restart_counts.get(stream_name, 0) + 1
        self._stream_last_restart[stream_name] = time.time()
    
    def is_stream_stale(self, stream_name: str) -> bool:
        """Check if a stream's data is stale."""
        last_update = self._stream_last_update.get(stream_name)
        if last_update is None:
            return True
        return (time.time() - last_update) > TELEMETRY_STALE_THRESHOLD
    
    def get_stream_health(self) -> Dict[str, dict]:
        """Get health status for all streams."""
        return {
            stream: {
                "last_update": self._stream_last_update.get(stream),
                "is_stale": self.is_stream_stale(stream),
                "restart_count": self._stream_restart_counts.get(stream, 0)
            }
            for stream in self._stream_restart_counts.keys()
        }
    
    async def set_telemetry_rates(self):
        """
        Set telemetry update rates to prevent callback queue overflow.
        
        MAVSDK sends telemetry at very high rates by default (50Hz+).
        We limit rates to prevent the callback queue from backing up.
        """
        if not self.drone_service.drone:
            return
        
        telemetry = self.drone_service.drone.telemetry
        
        # Set rates (Hz) - lower rates to prevent callback queue overflow
        # Each rate is set individually to handle partial failures
        rates_to_set = [
            ("position", 10.0),
            ("battery", 2.0),
            ("gps_info", 2.0),
            ("in_air", 2.0),
            ("landed_state", 2.0),
            ("velocity_ned", 10.0),
            ("attitude", 5.0),
            ("altitude", 10.0),
        ]
        
        for rate_name, rate_hz in rates_to_set:
            try:
                method = getattr(telemetry, f"set_rate_{rate_name}", None)
                if method:
                    await method(rate_hz)
                    logger.debug(f"Set {rate_name} rate to {rate_hz}Hz")
            except Exception as e:
                logger.debug(f"Could not set {rate_name} rate: {e}")
        
    
    def start_broadcaster(self):
        """Start the broadcaster task that sends telemetry to WebSocket clients."""
        if self._broadcaster_task and not self._broadcaster_task.done():
            return
        self._broadcaster_task = asyncio.create_task(self._broadcaster_loop())
        logger.debug("Telemetry broadcaster started")
    
    def stop_broadcaster(self):
        """Stop the broadcaster task."""
        if self._broadcaster_task and not self._broadcaster_task.done():
            self._broadcaster_task.cancel()
            logger.debug("Telemetry broadcaster stopped")
    
    async def _broadcaster_loop(self):
        """
        Separate broadcaster loop that reads latest values and sends to clients.
        Runs at fixed 10Hz, completely decoupled from MAVSDK stream consumption.
        """
        broadcast_interval = 0.1  # 10Hz
        last_battery_broadcast = 0.0
        battery_broadcast_interval = 0.5  # 2Hz for battery (slower)
        
        try:
            while not self.connection_manager.is_shutdown_requested():
                await asyncio.sleep(broadcast_interval)
                
                if not self.connection_manager.connections:
                    continue
                
                current_time = time.time()
                
                # Broadcast position telemetry
                # Note: position() includes altitude, so we use _latest_position for everything
                if self._latest_position is not None:
                    try:
                        lat = self._latest_position.latitude_deg
                        lon = self._latest_position.longitude_deg
                        relative_alt = self._latest_position.relative_altitude_m
                        absolute_alt = self._latest_position.absolute_altitude_m
                        
                        telemetry_data = {
                            "type": "telemetry",
                            "timestamp": datetime.now().isoformat(),
                            "position": {
                                "latitude": lat,
                                "longitude": lon,
                                "altitude_relative": relative_alt,
                                "altitude_absolute": absolute_alt
                            }
                        }
                        
                        # Add velocity if available
                        if self._latest_velocity:
                            try:
                                vn = self._latest_velocity.velocity.north_m_s
                                ve = self._latest_velocity.velocity.east_m_s
                                vd = self._latest_velocity.velocity.down_m_s
                                if math.isfinite(vn) and math.isfinite(ve) and math.isfinite(vd):
                                    telemetry_data["velocity"] = {"resultant": math.sqrt(vn**2 + ve**2 + vd**2)}
                            except (AttributeError, Exception):
                                pass
                        
                        # Add heading if available
                        if self._latest_heading is not None:
                            telemetry_data["heading"] = self._latest_heading
                        
                        telemetry_data = sanitize_for_json(telemetry_data)
                        await self.broadcaster.broadcast(telemetry_data)
                    except Exception as e:
                        logger.debug(f"Position broadcast error: {e}")
                
                # Broadcast battery at lower rate
                if (current_time - last_battery_broadcast) >= battery_broadcast_interval:
                    last_battery_broadcast = current_time
                    if self._latest_battery is not None:
                        try:
                            battery_data = {
                                "type": "battery",
                                "timestamp": datetime.now().isoformat(),
                                "battery": {
                                    "percentage": self._latest_battery.remaining_percent,
                                    "voltage": self._latest_battery.voltage_v,
                                    "current": self._latest_battery.current_battery_a
                                }
                            }
                            battery_data = sanitize_for_json(battery_data)
                            await self.broadcaster.broadcast(battery_data)
                        except Exception as e:
                            logger.debug(f"Battery broadcast error: {e}")
                
                # Broadcast flight mode
                if self._latest_flight_mode is not None:
                    try:
                        mode_data = {
                            "type": "flight_mode",
                            "timestamp": datetime.now().isoformat(),
                            "flight_mode": self._latest_flight_mode.name if hasattr(self._latest_flight_mode, 'name') else str(self._latest_flight_mode)
                        }
                        await self.broadcaster.broadcast(mode_data)
                    except Exception as e:
                        logger.debug(f"Flight mode broadcast error: {e}")
                
                # Broadcast armed status
                if self._latest_armed is not None:
                    try:
                        armed_data = {
                            "type": "armed",
                            "timestamp": datetime.now().isoformat(),
                            "armed": self._latest_armed
                        }
                        await self.broadcaster.broadcast(armed_data)
                    except Exception as e:
                        logger.debug(f"Armed broadcast error: {e}")
                        
        except asyncio.CancelledError:
            logger.debug("Broadcaster loop cancelled")
        except Exception as e:
            logger.error(f"Broadcaster loop error: {e}")
    
    async def stream_position(self):
        """
        Fast consumer for position/velocity streams.
        Only stores latest values and updates safety manager - NO broadcasting here.
        
        Note: position() already includes altitude, so we don't need a separate altitude stream.
        This reduces the number of subscriptions to prevent callback queue overflow.
        """
        stream_name = "position"
        should_restart = False
        
        if not self.drone_service.drone:
            return
        
        try:
            # Subscribe to 3 streams (position includes altitude)
            position_stream = self.drone_service.drone.telemetry.position()
            velocity_stream = self.drone_service.drone.telemetry.position_velocity_ned()
            attitude_stream = self.drone_service.drone.telemetry.attitude_euler()
            
            # Fast attitude consumer - updates heading in safety manager
            async def consume_attitude():
                try:
                    async for attitude in attitude_stream:
                        if self.connection_manager.is_shutdown_requested():
                            break
                        # Update heading (yaw) in safety manager
                        # attitude_euler gives: roll, pitch, yaw in degrees
                        self.drone_service.safety_manager.update_heading(attitude.yaw_deg)
                        # Store heading for broadcasting to frontend
                        self._latest_heading = attitude.yaw_deg
                except Exception as e:
                    if not self.connection_manager.is_shutdown_requested():
                        error_str = str(e)
                        if "Socket closed" not in error_str and "UNAVAILABLE" not in error_str:
                            logger.debug(f"Attitude stream error: {e}")
            
            # Fast position consumer - stores position and updates safety manager
            async def consume_position():
                nonlocal should_restart
                try:
                    async for pos in position_stream:
                        if self.connection_manager.is_shutdown_requested():
                            break
                        
                        # Store latest position (includes altitude)
                        self._latest_position = pos
                        
                        # Update stream health
                        self._update_stream_health(stream_name)
                        
                        # Update safety manager with position data
                        # position() includes: latitude_deg, longitude_deg, absolute_altitude_m, relative_altitude_m
                        self.drone_service.safety_manager.update_position(
                            pos.latitude_deg,
                            pos.longitude_deg,
                            pos.absolute_altitude_m,
                            relative_alt=pos.relative_altitude_m
                        )
                        
                        # Check if auto-land needed
                        if self.drone_service.safety_manager.should_auto_land():
                            logger.warning("Battery critical - triggering auto-land")
                            asyncio.create_task(self._trigger_auto_land())
                            
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    error_str = str(e)
                    if not self.connection_manager.is_shutdown_requested():
                        if "Socket closed" not in error_str and "UNAVAILABLE" not in error_str:
                            logger.error(f"Position stream error: {e}")
                            should_restart = True
            
            # Fast velocity consumer - just store latest
            async def consume_velocity():
                try:
                    async for vel in velocity_stream:
                        if self.connection_manager.is_shutdown_requested():
                            break
                        self._latest_velocity = vel
                except Exception as e:
                    if not self.connection_manager.is_shutdown_requested():
                        error_str = str(e)
                        if "Socket closed" not in error_str and "UNAVAILABLE" not in error_str:
                            logger.debug(f"Velocity stream error: {e}")
            
            # Run all consumers concurrently
            await asyncio.gather(
                consume_position(),
                consume_velocity(),
                consume_attitude(),
                return_exceptions=True
            )
                
        except asyncio.CancelledError:
            logger.debug("Position stream cancelled")
            raise
        except Exception as e:
            error_str = str(e)
            if not self.connection_manager.is_shutdown_requested():
                if "Socket closed" not in error_str and "UNAVAILABLE" not in error_str:
                    logger.error(f"Position stream error: {e}")
                    should_restart = True
        finally:
            if should_restart and self._should_restart_stream(stream_name) and not self.connection_manager.is_shutdown_requested():
                self._record_stream_restart(stream_name)
                backoff = TELEMETRY_STREAM_RESTART_BACKOFF * (2 ** (self._stream_restart_counts[stream_name] - 1))
                backoff = min(backoff, 10.0)
                logger.warning(f"Restarting {stream_name} stream in {backoff:.1f}s")
                await asyncio.sleep(backoff)
                if not self.connection_manager.is_shutdown_requested():
                    asyncio.create_task(self.stream_position())
    
    async def _trigger_auto_land(self):
        """Fire-and-forget auto-land trigger."""
        try:
            await self.drone_service.drone.action.land()
        except Exception as e:
            logger.error(f"Auto-land failed: {e}")
    
    async def stream_battery(self):
        """
        Fast consumer for battery stream.
        Only stores latest value and updates safety manager - NO broadcasting here.
        """
        stream_name = "battery"
        should_restart = False
        
        if not self.drone_service.drone:
            return
        
        try:
            async for battery in self.drone_service.drone.telemetry.battery():
                if self.connection_manager.is_shutdown_requested():
                    break
                
                # Store latest (for broadcaster)
                self._latest_battery = battery
                
                # Update stream health and safety manager
                self._update_stream_health(stream_name)
                self.drone_service.safety_manager.update_battery(battery.remaining_percent)
                
        except asyncio.CancelledError:
            logger.debug("Battery stream cancelled")
            raise
        except Exception as e:
            error_str = str(e)
            if not self.connection_manager.is_shutdown_requested():
                if "Socket closed" not in error_str and "UNAVAILABLE" not in error_str:
                    logger.error(f"Battery stream error: {e}")
                    should_restart = True
        finally:
            if should_restart and self._should_restart_stream(stream_name) and not self.connection_manager.is_shutdown_requested():
                self._record_stream_restart(stream_name)
                backoff = TELEMETRY_STREAM_RESTART_BACKOFF * (2 ** (self._stream_restart_counts[stream_name] - 1))
                backoff = min(backoff, 10.0)
                logger.warning(f"Restarting {stream_name} stream in {backoff:.1f}s")
                await asyncio.sleep(backoff)
                if not self.connection_manager.is_shutdown_requested():
                    asyncio.create_task(self.stream_battery())
    
    async def stream_flight_mode(self):
        """
        Fast consumer for flight mode stream.
        Only stores latest value - NO broadcasting here.
        """
        stream_name = "flight_mode"
        should_restart = False
        
        if not self.drone_service.drone:
            return
        
        try:
            async for flight_mode in self.drone_service.drone.telemetry.flight_mode():
                if self.connection_manager.is_shutdown_requested():
                    break
                
                # Store latest (for broadcaster)
                self._latest_flight_mode = flight_mode
                
                # Update stream health
                self._update_stream_health(stream_name)
                
        except asyncio.CancelledError:
            logger.debug("Flight mode stream cancelled")
            raise
        except Exception as e:
            error_str = str(e)
            if not self.connection_manager.is_shutdown_requested():
                if "Socket closed" not in error_str and "UNAVAILABLE" not in error_str:
                    logger.error(f"Flight mode stream error: {e}")
                    should_restart = True
        finally:
            if should_restart and self._should_restart_stream(stream_name) and not self.connection_manager.is_shutdown_requested():
                self._record_stream_restart(stream_name)
                backoff = TELEMETRY_STREAM_RESTART_BACKOFF * (2 ** (self._stream_restart_counts[stream_name] - 1))
                backoff = min(backoff, 10.0)
                logger.warning(f"Restarting {stream_name} stream in {backoff:.1f}s")
                await asyncio.sleep(backoff)
                if not self.connection_manager.is_shutdown_requested():
                    asyncio.create_task(self.stream_flight_mode())
    
    async def stream_armed_status(self):
        """
        Fast consumer for armed status stream.
        Only stores latest value and updates safety manager - NO broadcasting here.
        """
        stream_name = "armed_status"
        should_restart = False
        
        if not self.drone_service.drone:
            return
        
        try:
            async for is_armed in self.drone_service.drone.telemetry.armed():
                if self.connection_manager.is_shutdown_requested():
                    break
                
                # Store latest (for broadcaster)
                self._latest_armed = is_armed
                
                # Update stream health and safety manager
                self._update_stream_health(stream_name)
                self.drone_service.safety_manager.is_armed = is_armed
                
        except asyncio.CancelledError:
            logger.debug("Armed status stream cancelled")
            raise
        except Exception as e:
            error_str = str(e)
            if not self.connection_manager.is_shutdown_requested():
                if "Socket closed" not in error_str and "UNAVAILABLE" not in error_str:
                    logger.error(f"Armed status stream error: {e}")
                    should_restart = True
        finally:
            if should_restart and self._should_restart_stream(stream_name) and not self.connection_manager.is_shutdown_requested():
                self._record_stream_restart(stream_name)
                backoff = TELEMETRY_STREAM_RESTART_BACKOFF * (2 ** (self._stream_restart_counts[stream_name] - 1))
                backoff = min(backoff, 10.0)
                logger.warning(f"Restarting {stream_name} stream in {backoff:.1f}s")
                await asyncio.sleep(backoff)
                if not self.connection_manager.is_shutdown_requested():
                    asyncio.create_task(self.stream_armed_status())
