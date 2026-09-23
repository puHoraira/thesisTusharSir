"""
Connection management for drone service.
"""

import asyncio
import logging
import time
from typing import Optional, Callable, Awaitable

import grpc

from mavsdk import System

from safety import SafetyManager, SafetyConfig
from config import (
    HARDWARE_MODE, 
    HARDWARE_TIMEOUT_MULTIPLIER,
    CONNECTION_MONITOR_INTERVAL,
    CONNECTION_LOSS_TIMEOUT,
    CONNECTION_RECONNECT_MAX_ATTEMPTS,
    CONNECTION_RECONNECT_BACKOFF_BASE,
    GPS_LOSS_TIMEOUT,
    GPS_LOSS_ACTION,
    GPS_MIN_SATELLITES
)

logger = logging.getLogger("drone_connection_manager")


class ConnectionManager:
    """Manages drone connection and initial setup."""
    
    def __init__(self, safety_manager: SafetyManager, shutdown_event: Optional[asyncio.Event] = None):
        self.safety_manager = safety_manager
        self.drone: Optional[System] = None
        self.connected: bool = False
        
        # External shutdown event (from main.py) for early shutdown detection
        self._external_shutdown_event = shutdown_event
        
        # Connection health monitoring
        self._connection_monitor_task: Optional[asyncio.Task] = None
        self._gps_monitor_task: Optional[asyncio.Task] = None
        self._last_heartbeat_time: Optional[float] = None
        self._connection_lost_callback: Optional[Callable[[], Awaitable[None]]] = None
        self._connection_restored_callback: Optional[Callable[[], Awaitable[None]]] = None
        self._reconnect_attempts: int = 0
        self._system_address: str = ""
        
        # GPS health tracking
        self.gps_healthy: bool = False
        self.gps_fix_type: int = 0
        self.gps_num_satellites: int = 0
        self._last_gps_time: Optional[float] = None
        self._gps_loss_callback: Optional[Callable[[], Awaitable[None]]] = None
        
        # Shutdown flag
        self._shutdown_requested: bool = False
    
    def set_shutdown_event(self, shutdown_event: asyncio.Event):
        """Set external shutdown event for early shutdown detection."""
        self._external_shutdown_event = shutdown_event
    
    def _is_shutting_down(self) -> bool:
        """Check if shutdown has been requested either locally or externally."""
        if self._shutdown_requested:
            return True
        if self._external_shutdown_event and self._external_shutdown_event.is_set():
            return True
        return False
    
    def set_connection_lost_callback(self, callback: Callable[[], Awaitable[None]]):
        """Set callback to be called when connection is lost during flight."""
        self._connection_lost_callback = callback
    
    def set_connection_restored_callback(self, callback: Callable[[], Awaitable[None]]):
        """Set callback to be called when connection is restored."""
        self._connection_restored_callback = callback
    
    def set_gps_loss_callback(self, callback: Callable[[], Awaitable[None]]):
        """Set callback to be called when GPS is lost during flight."""
        self._gps_loss_callback = callback
    
    async def connect(self, system_address: str = "udpin://0.0.0.0:14540") -> bool:
        """
        Connect to drone via MAVSDK with retry logic and heartbeat validation
        
        Args:
            system_address: MAVLink connection string (e.g., "udpin://0.0.0.0:14540")
        
        Returns:
            True if connection successful, False otherwise
        """
        self._system_address = system_address
        max_retries = 3  # Always retry 3 times regardless of mode
        retry_delay = 2.0
        
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.info(f"Connection retry {attempt + 1}/{max_retries} after {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
                
                logger.info(f"Connecting to drone at {system_address}...")
                self.drone = System()
                await self.drone.connect(system_address=system_address)
                
                # Wait for system to be discovered with timeout
                logger.debug("Waiting for drone to be discovered...")
                
                try:
                    async for state in self.drone.core.connection_state():
                        if state.is_connected:
                            logger.debug("Drone connection state: connected")
                            self.connected = True
                            break
                except asyncio.CancelledError:
                    logger.info("Connection wait cancelled")
                    self.connected = False
                    return False
                
                if not self.connected:
                    logger.warning(f"Drone not discovered within timeout")
                    continue  # Try next attempt
                
                # Validate heartbeat (ensure MAVLink communication is stable)
                logger.debug("Validating MAVLink heartbeat...")
                heartbeat_timeout = 5.0 * HARDWARE_TIMEOUT_MULTIPLIER
                try:
                    health = await asyncio.wait_for(
                        self.drone.telemetry.health().__anext__(),
                        timeout=heartbeat_timeout
                    )
                    logger.debug("Heartbeat validated - system health OK")
                except asyncio.TimeoutError:
                    logger.warning("Heartbeat validation timeout - connection may be unstable")
                    if HARDWARE_MODE:
                        # In hardware mode, unstable connection is a hard failure
                        self.connected = False
                        continue
                    # In SITL, continue anyway (SITL can be slow to respond)
                
                # Get initial position to set home (with timeout)
                position_timeout = 5.0 * HARDWARE_TIMEOUT_MULTIPLIER
                try:
                    position = await asyncio.wait_for(
                        self.drone.telemetry.position().__anext__(),
                        timeout=position_timeout
                    )
                    self.safety_manager.set_home_position(
                        position.latitude_deg,
                        position.longitude_deg,
                        position.absolute_altitude_m
                    )
                    logger.debug(f"Home position set: {position.latitude_deg:.6f}, {position.longitude_deg:.6f}")
                except asyncio.TimeoutError:
                    logger.warning("Position timeout - home position not set (will use first position)")
                except asyncio.CancelledError:
                    logger.debug("Position wait cancelled")
                    raise
                
                logger.info(f"Connection successful (attempt {attempt + 1}/{max_retries})")
                
                # DISABLE telemetry rate setting - it overwhelms Pixhawk and breaks the connection
                # Pixhawk default rates are fine for our use case
                # await self._set_telemetry_rates()
                logger.debug("Using Pixhawk default telemetry rates")
                
                # Start connection health monitoring
                self._last_heartbeat_time = time.time()
                self._start_connection_monitor()
                self._start_gps_monitor()
                
                return self.connected
                
            except asyncio.CancelledError:
                logger.info("Connection cancelled")
                self.connected = False
                return False
            except Exception as e:
                logger.error(f"Connection attempt {attempt + 1} failed: {e}")
                self.connected = False
                if attempt == max_retries - 1:
                    logger.error(f"Failed to connect after {max_retries} attempts")
                    return False
        
        return False
    
    async def disconnect(self):
        """Disconnect from drone"""
        self._shutdown_requested = True
        
        # Stop monitoring tasks gracefully
        await self._stop_monitoring_tasks()
        
        if self.drone and self.connected:
            try:
                # Try to land with timeout
                await asyncio.wait_for(self.drone.action.land(), timeout=2.0)
                logger.info("Landing drone on disconnect")
            except asyncio.TimeoutError:
                logger.warning("Land command timeout")
            except Exception as e:
                logger.debug(f"Land command failed: {e}")
        
        self.connected = False
        
        # Clean up drone instance
        if self.drone:
            try:
                # Force close connection
                self.drone = None
            except:
                pass
        
        self._shutdown_requested = False
    
    async def _set_telemetry_rates(self):
        """
        Set telemetry update rates IMMEDIATELY after connection.
        
        This prevents MAVSDK callback queue overflow by telling PX4 
        to send data at lower rates. Must be called before any 
        telemetry subscriptions are created.
        """
        if not self.drone:
            return
        
        telemetry = self.drone.telemetry
        
        # CRITICAL: Set VERY conservative rates for hardware to prevent connection overload
        # These rates are MUCH lower than before to avoid MAVProxy "no link" crashes
        rates = [
            ("position", 5.0),  # Reduced from 10
            ("position_velocity_ned", 5.0),  # Reduced from 10
            ("attitude", 2.0),  # Reduced from 5
            ("velocity_ned", 5.0),  # Reduced from 10
            ("battery", 1.0),  # Reduced from 2
            ("gps_info", 1.0),  # Reduced from 2
            ("in_air", 1.0),  # Reduced from 2
            ("landed_state", 1.0),  # Reduced from 2
            ("altitude", 5.0),  # Reduced from 10
            ("home", 0.5),  # Reduced from 1
            ("actuator_control_target", 0.0),  # Disable
            ("actuator_output_status", 0.0),   # Disable
        ]
        
        success_count = 0
        for rate_name, rate_hz in rates:
            try:
                method = getattr(telemetry, f"set_rate_{rate_name}", None)
                if method:
                    await method(rate_hz)
                    success_count += 1
                    # Small delay between rate changes to avoid overwhelming MAVLink
                    await asyncio.sleep(0.1)
            except Exception as e:
                logger.debug(f"Could not set rate for {rate_name}: {e}")
        
        logger.debug(f"Telemetry rates configured ({success_count}/{len(rates)} rates set)")
    
    def _start_connection_monitor(self):
        """Start background task to monitor connection health."""
        if self._connection_monitor_task and not self._connection_monitor_task.done():
            logger.debug("Connection monitor already running")
            return
        
        async def connection_monitor_loop():
            """
            Continuously monitor connection state using a single subscription.
            Uses async for to avoid creating new subscriptions each iteration.
            """
            logger.debug("Starting connection health monitor")
            was_connected = True
            disconnect_started_at: Optional[float] = None
            
            try:
                if not self.drone:
                    return
                
                # Create subscription ONCE, then iterate
                async for state in self.drone.core.connection_state():
                    if self._is_shutting_down():
                        logger.debug("Shutdown detected, stopping connection monitor")
                        break
                    
                    if state.is_connected:
                        self._last_heartbeat_time = time.time()
                        # Reset transient disconnect timer when link comes back.
                        if disconnect_started_at is not None:
                            logger.info("⚡ Quick recovery - PX4 reconnected before timeout")
                        disconnect_started_at = None
                        
                        if not was_connected:
                            logger.info("✅ Connection restored - PX4 heartbeats detected")
                            was_connected = True
                            self._reconnect_attempts = 0
                            if self._connection_restored_callback:
                                try:
                                    await self._connection_restored_callback()
                                except Exception as cb_err:
                                    logger.error(f"Connection restored callback error: {cb_err}")
                    else:
                        now = time.time()
                        if disconnect_started_at is None:
                            disconnect_started_at = now
                            logger.warning(
                                f"Connection drop detected, waiting {CONNECTION_LOSS_TIMEOUT:.1f}s before declaring lost"
                            )

                        # Ignore short blips and only declare lost after timeout.
                        if was_connected and (now - disconnect_started_at) >= CONNECTION_LOSS_TIMEOUT:
                            logger.warning("Connection lost detected via connection state")
                            was_connected = False
                            await self._handle_connection_loss()
                        elif disconnect_started_at and not was_connected:
                            # Still disconnected but within timeout - waiting
                            elapsed = now - disconnect_started_at
                            logger.debug(f"Waiting for reconnection... ({elapsed:.1f}s / {CONNECTION_LOSS_TIMEOUT:.1f}s)")
                        
            except asyncio.CancelledError:
                logger.debug("Connection monitor cancelled")
            except (asyncio.CancelledError, grpc.aio.AioRpcError):
                # Expected during shutdown - socket closes or task cancelled
                if not self._is_shutting_down():
                    logger.warning("Connection monitor stopped unexpectedly")
            except Exception as e:
                if not self._is_shutting_down():
                    logger.error(f"Connection monitor failed: {e}")
            finally:
                logger.debug("Connection monitor stopped")
        
        self._connection_monitor_task = asyncio.create_task(connection_monitor_loop())
        logger.debug("Connection monitor started")
    
    def _start_gps_monitor(self):
        """Start background task to monitor GPS health."""
        if self._gps_monitor_task and not self._gps_monitor_task.done():
            logger.debug("GPS monitor already running")
            return
        
        async def gps_monitor_loop():
            """
            Continuously monitor GPS status using a single subscription.
            Uses async for to avoid creating new subscriptions each iteration.
            """
            logger.debug("Starting GPS health monitor")
            gps_was_healthy = True
            
            try:
                if not self.drone:
                    return
                
                # Create subscription ONCE, then iterate
                async for gps_info in self.drone.telemetry.gps_info():
                    if self._is_shutting_down():
                        logger.debug("Shutdown detected, stopping GPS monitor")
                        break
                    
                    self.gps_fix_type = gps_info.fix_type.value if hasattr(gps_info.fix_type, 'value') else int(gps_info.fix_type)
                    self.gps_num_satellites = gps_info.num_satellites
                    self._last_gps_time = time.time()
                    
                    # Check if GPS is healthy (fix type >= 3 is 3D fix, and enough satellites)
                    is_healthy = self.gps_fix_type >= 3 and self.gps_num_satellites >= GPS_MIN_SATELLITES
                    
                    if is_healthy:
                        self.gps_healthy = True
                        if not gps_was_healthy:
                            logger.info(f"GPS restored: {self.gps_num_satellites} satellites")
                            gps_was_healthy = True
                    else:
                        if gps_was_healthy and self.safety_manager.is_armed:
                            logger.warning(f"GPS degraded: fix_type={self.gps_fix_type}, satellites={self.gps_num_satellites}")
                            gps_was_healthy = False
                            self.gps_healthy = False
                            await self._handle_gps_loss()
                        elif not gps_was_healthy:
                            self.gps_healthy = False
                        
            except asyncio.CancelledError:
                logger.debug("GPS monitor cancelled")
            except (asyncio.CancelledError, grpc.aio.AioRpcError):
                # Expected during shutdown - socket closes or task cancelled
                if not self._is_shutting_down():
                    logger.warning("GPS monitor stopped unexpectedly")
            except Exception as e:
                if not self._is_shutting_down():
                    logger.error(f"GPS monitor failed: {e}")
            finally:
                logger.debug("GPS monitor stopped")
        
        self._gps_monitor_task = asyncio.create_task(gps_monitor_loop())
        logger.debug("GPS monitor started")
    
    async def _handle_connection_loss(self):
        """Handle connection loss during flight."""
        self.connected = False
        
        # Don't attempt reconnection during shutdown
        if self._is_shutting_down():
            logger.debug("Skipping connection loss handling during shutdown")
            return
        
        # Call the connection lost callback
        if self._connection_lost_callback:
            try:
                await self._connection_lost_callback()
            except Exception as e:
                logger.error(f"Connection lost callback error: {e}")
        
        # Attempt auto-reconnection if armed (drone might be in flight)
        if self.safety_manager.is_armed and self._reconnect_attempts < CONNECTION_RECONNECT_MAX_ATTEMPTS:
            self._reconnect_attempts += 1
            
            # Calculate backoff delay with exponential increase
            backoff_delay = CONNECTION_RECONNECT_BACKOFF_BASE * (2 ** (self._reconnect_attempts - 1))
            backoff_delay = min(backoff_delay, 30.0)  # Cap at 30 seconds
            
            logger.warning(f"Attempting auto-reconnection ({self._reconnect_attempts}/{CONNECTION_RECONNECT_MAX_ATTEMPTS}) in {backoff_delay:.1f}s...")
            
            await asyncio.sleep(backoff_delay)
            
            if not self._is_shutting_down():
                success = await self.connect(self._system_address)
                if success:
                    logger.info("Auto-reconnection successful")
                else:
                    logger.error(f"Auto-reconnection attempt {self._reconnect_attempts} failed")
        elif self.safety_manager.is_armed:
            logger.error(f"CRITICAL: Connection lost while drone is armed! Max reconnection attempts ({CONNECTION_RECONNECT_MAX_ATTEMPTS}) exhausted.")
            logger.error(f"Last known position: {self.safety_manager.current_position}")
    
    async def _handle_gps_loss(self):
        """Handle GPS loss during flight - execute configured failsafe action."""
        # Don't execute failsafe actions during shutdown
        if self._is_shutting_down():
            logger.debug("Skipping GPS loss handling during shutdown")
            return
        
        if self._gps_loss_callback:
            try:
                await self._gps_loss_callback()
            except Exception as e:
                logger.error(f"GPS loss callback error: {e}")
        
        # Execute GPS loss action if armed
        if self.safety_manager.is_armed and self.drone:
            logger.error(f"CRITICAL: GPS lost while drone is armed!")
            logger.error(f"GPS fix type: {self.gps_fix_type}, satellites: {self.gps_num_satellites}")
            
            try:
                action = GPS_LOSS_ACTION.upper()
                if action == "LAND":
                    logger.warning("GPS lost - initiating emergency landing")
                    await self.drone.action.land()
                elif action == "RTL":
                    logger.warning("GPS lost - initiating return to launch")
                    await self.drone.action.return_to_launch()
                elif action == "HOLD":
                    logger.warning("GPS lost - attempting to hold position")
                    await self.drone.action.hold()
                else:
                    logger.warning(f"GPS lost - unknown action: {action}, defaulting to LAND")
                    await self.drone.action.land()
            except Exception as e:
                logger.error(f"GPS loss failsafe action failed: {e}")
    
    async def _stop_monitoring_tasks(self):
        """Stop all monitoring tasks gracefully."""
        tasks_to_stop = []
        task_names = []
        
        if self._connection_monitor_task and not self._connection_monitor_task.done():
            self._connection_monitor_task.cancel()
            tasks_to_stop.append(self._connection_monitor_task)
            task_names.append("connection monitor")
        
        if self._gps_monitor_task and not self._gps_monitor_task.done():
            self._gps_monitor_task.cancel()
            tasks_to_stop.append(self._gps_monitor_task)
            task_names.append("GPS monitor")
        
        if tasks_to_stop:
            # Wait for tasks to complete with a timeout
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks_to_stop, return_exceptions=True),
                    timeout=2.0
                )
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for monitoring tasks to stop")
            
            logger.info(f"Stopped monitoring tasks: {', '.join(task_names)}")
