"""
Main drone service class that orchestrates all drone operations.
"""

import asyncio
import logging
from typing import Optional
from datetime import datetime

import mavsdk
from mavsdk import System

from safety import SafetyManager, SafetyConfig
from .connection import ConnectionManager
from .offboard import OffboardManager
from .flight import FlightOperations
from .velocity import VelocityController
from .bgtasks import BackgroundTasks
from config import DEFAULT_TAKEOFF_ALTITUDE

logger = logging.getLogger("drone_service")


class DroneService:
    """Service for managing drone connection and control"""
    
    def __init__(self, safety_config: SafetyConfig = None):
        self.drone: Optional[System] = None
        self.connected: bool = False
        
        # Initialize safety manager
        self.safety_manager = SafetyManager(safety_config or SafetyConfig())
        
        # Initialize component managers
        self.connection_manager = ConnectionManager(self.safety_manager)
        self.offboard_manager = OffboardManager(None)
        self.velocity_controller = VelocityController(self.safety_manager)
        self.flight_ops = FlightOperations(None, self.safety_manager, self.offboard_manager)
        self.background_tasks = BackgroundTasks(None, self.velocity_controller, self.offboard_manager)
        self.background_tasks.set_safety_manager(self.safety_manager)  # Set early for monitors
        
        # Set up connection callbacks
        self.connection_manager.set_connection_lost_callback(self._on_connection_lost)
        self.connection_manager.set_connection_restored_callback(self._on_connection_restored)
        self.connection_manager.set_gps_loss_callback(self._on_gps_loss)
        
        # Connection loss tracking
        self._connection_lost_in_flight: bool = False
        
        # Update drone references in all managers
        self._update_drone_references()
    
    def _update_drone_references(self):
        """Update drone instance references in all managers."""
        self.connection_manager.drone = self.drone
        self.offboard_manager.set_drone(self.drone)
        self.flight_ops.set_drone(self.drone)
        self.background_tasks.set_drone(self.drone)
    
    async def _on_connection_lost(self):
        """
        Handle connection loss during flight.
        
        Emergency sequence:
        1. Set connected flag to False
        2. If armed/in flight: immediately send zero velocity
        3. Attempt to send land command before connection fully drops
        4. Stop background tasks to prevent errors
        """
        logger.warning("Connection lost callback triggered")
        self.connected = False
        self._connection_lost_in_flight = self.safety_manager.is_armed
        
        if self._connection_lost_in_flight:
            logger.warning("Connection lost while armed - executing emergency sequence")
            
            # Step 1: Immediately send zero velocity to stop movement
            try:
                if self.drone and self.offboard_manager.offboard_initialized:
                    await self.drone.offboard.set_velocity_body(
                        mavsdk.offboard.VelocityBodyYawspeed(0.0, 0.0, 0.0, 0.0)
                    )
                    logger.debug("Sent zero velocity on connection loss")
            except Exception as e:
                logger.error(f"Failed to send zero velocity on connection loss: {e}")
            
            # Step 2: Try to send land command before connection fully drops
            try:
                if self.drone:
                    # Stop offboard mode first
                    try:
                        await asyncio.wait_for(
                            self.drone.offboard.stop(),
                            timeout=0.5
                        )
                    except:
                        pass  # May fail if already not in offboard
                    
                    # Send land command
                    await asyncio.wait_for(
                        self.drone.action.land(),
                        timeout=1.0
                    )
                    logger.warning("Sent emergency land command on connection loss")
            except asyncio.TimeoutError:
                logger.error("Land command timeout on connection loss")
            except Exception as e:
                logger.error(f"Failed to send land command on connection loss: {e}")
            
            # Step 3: Stop background tasks to prevent errors
            self.background_tasks.stop_all_tasks()
            
            # Reset velocity state
            self.velocity_controller.reset_velocity()
        else:
            logger.debug("Connection lost while disarmed - no emergency action needed")
    
    async def _on_connection_restored(self):
        """Handle connection restoration."""
        logger.info("Connection restored callback triggered")
        self.connected = True
        self.drone = self.connection_manager.drone
        self._update_drone_references()
        
        # If we lost connection in flight, attempt to recover
        if self._connection_lost_in_flight:
            logger.debug("Attempting to recover flight state after reconnection")
            try:
                success, message = await self.detect_and_recover_flight_state()
                if success:
                    logger.debug(f"Flight state recovery successful: {message}")
                else:
                    logger.warning(f"Flight state recovery failed: {message}")
            except Exception as e:
                logger.error(f"Flight state recovery error: {e}")
            finally:
                self._connection_lost_in_flight = False
    
    async def _on_gps_loss(self):
        """Handle GPS loss during flight."""
        logger.warning("GPS loss callback triggered")
        # Additional handling can be added here
        # The connection manager already handles the GPS loss action
    
    async def connect(self, system_address: str = "udpin://0.0.0.0:14540") -> bool:
        """
        Connect to drone via MAVSDK with retry logic and heartbeat validation
        
        Args:
            system_address: MAVLink connection string (e.g., "udpin://0.0.0.0:14540")
        
        Returns:
            True if connection successful, False otherwise
        """
        success = await self.connection_manager.connect(system_address)
        if success:
            self.drone = self.connection_manager.drone
            self.connected = self.connection_manager.connected
            self._update_drone_references()
        return success
    
    async def disconnect(self):
        """Disconnect from drone"""
        # Stop all background tasks
        self.background_tasks.stop_all_tasks()
        
        # Stop offboard mode
        await self.offboard_manager.stop()
        
        # Disconnect
        await self.connection_manager.disconnect()
        
        self.connected = False
        self.offboard_manager.reset_state()
        self.drone = None
        self._update_drone_references()
    
    async def detect_and_recover_flight_state(self) -> tuple[bool, str]:
        """
        Detect if drone is already in flight (reconnection scenario) and attempt recovery.
        
        This is called after backend reconnects to PX4 that stayed alive.
        Checks if drone is armed and in the air, then attempts to resume offboard control.
        
        Returns:
            (recovery_attempted: bool, message: str)
        """
        result = await self.flight_ops.detect_and_recover_flight_state()
        if result[0]:
            # Start background tasks if recovery was successful
            self.background_tasks.start_setpoint_streamer()
            self.background_tasks.start_offboard_monitor()
            self.background_tasks.start_state_sync(self.safety_manager)
        return result
    
    async def resume_offboard_mode(self) -> tuple[bool, str]:
        """
        Resume offboard mode on a drone that's already in flight.
        
        This is specifically designed for reconnection scenarios where:
        - Backend restarted but PX4 stayed alive
        - Drone is in HOLD mode (or similar) in the air
        - Need to transition to OFFBOARD without landing
        
        Returns:
            (success: bool, message: str)
        """
        success, message = await self.offboard_manager.resume()
        if success:
            # Start background tasks
            self.background_tasks.start_setpoint_streamer()
            self.background_tasks.start_offboard_monitor()
            self.background_tasks.start_state_sync(self.safety_manager)
        return success, message
    
    async def takeoff(self, altitude: float = None) -> tuple[bool, str]:
        """
        Arm and takeoff drone
        
        Args:
            altitude: Target altitude in meters
        
        Returns:
            (success: bool, message: str)
        """
        altitude = altitude if altitude is not None else DEFAULT_TAKEOFF_ALTITUDE
        success, message = await self.flight_ops.takeoff(altitude)
        if success:
            # Start background tasks after successful offboard initialization
            if self.offboard_manager.offboard_initialized:
                self.background_tasks.start_setpoint_streamer()
                self.background_tasks.start_offboard_monitor()
                self.background_tasks.start_state_sync(self.safety_manager)
        return success, message
    
    async def land(self) -> tuple[bool, str]:
        """
        Land the drone and clean up offboard mode
        
        Returns:
            (success: bool, message: str)
        """
        # Stop background tasks first
        self.background_tasks.stop_all_tasks()
        
        # Reset velocity state
        self.velocity_controller.reset_velocity()
        
        return await self.flight_ops.land()
    
    async def emergency_stop(self) -> tuple[bool, str]:
        """
        Emergency stop - immediately stop offboard and initiate landing.
        PX4 will automatically disarm after landing.
        
        Returns:
            (success: bool, message: str)
        """
        # Stop background tasks immediately
        self.background_tasks.stop_all_tasks()
        
        # Reset velocity state
        self.velocity_controller.reset_velocity()
        
        return await self.flight_ops.emergency_stop()
    
    async def set_velocity_body(self, vx: float, vy: float, vz: float, yaw_rate: float) -> tuple[bool, str]:
        """
        Set velocity in body frame (non-blocking state update).
        
        This method updates the target velocity state. The background setpoint streamer
        will continuously send this velocity to PX4 to maintain offboard mode.
        
        Args:
            vx: Forward velocity (m/s)
            vy: Right velocity (m/s)
            vz: Down velocity (m/s)
            yaw_rate: Yaw rate (deg/s)
        
        Returns:
            (success: bool, message: str)
        """
        return self.velocity_controller.set_velocity(
            vx, vy, vz, yaw_rate, 
            self.offboard_manager.offboard_initialized
        )
    
    async def send_zero_velocity(self) -> bool:
        """
        Send zero velocity to stop drone with retry logic.
        
        Returns:
            True if successful, False otherwise
        """
        if not self.drone or not self.connected or not self.offboard_manager.offboard_initialized:
            return False
        
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                await asyncio.wait_for(
                    self.drone.offboard.set_velocity_body(
                        mavsdk.offboard.VelocityBodyYawspeed(0.0, 0.0, 0.0, 0.0)
                    ),
                    timeout=0.5
                )
                self.velocity_controller.reset_velocity()
                # Command timeout - sent zero velocity (no log needed for routine operation)
                return True
            except asyncio.TimeoutError:
                logger.warning(f"Zero velocity send timeout (attempt {attempt + 1}/{max_attempts})")
            except Exception as e:
                logger.error(f"Failed to send zero velocity (attempt {attempt + 1}/{max_attempts}): {e}")
            
            if attempt < max_attempts - 1:
                await asyncio.sleep(0.1)
        
        return False
    
    async def command_timeout_handler(self):
        """
        Handle command timeout - stop drone if no commands received.
        
        Connection-aware enhancements:
        - Adjusts timeout based on connection and telemetry health
        - Extends timeout when connection issues detected
        - Shortens timeout when telemetry is degraded
        - Tracks consecutive failures to detect persistent issues
        """
        consecutive_failures = 0
        max_consecutive_failures = 5
        
        try:
            while True:
                await asyncio.sleep(0.05)  # Check every 50ms
                
                if self.velocity_controller.last_command_time is None:
                    continue
                
                elapsed = (datetime.now() - self.velocity_controller.last_command_time).total_seconds() * 1000
                
                # Calculate effective timeout based on conditions
                effective_timeout = self.velocity_controller.command_timeout_ms
                
                # Check connection health
                connection_healthy = self.connected and self.connection_manager.connected
                
                if not connection_healthy:
                    # Connection issues - extend timeout to avoid spam
                    effective_timeout *= 2.0
                
                # Check telemetry health
                telemetry_health = self.safety_manager.get_telemetry_health()
                if telemetry_health.get("is_degraded", False):
                    # Telemetry degraded - shorter timeout to stop drone faster
                    effective_timeout *= 0.75
                
                if elapsed > effective_timeout:
                    current_velocity = self.velocity_controller.get_current_velocity()
                    if current_velocity != {"vx": 0.0, "vy": 0.0, "vz": 0.0, "yaw_rate": 0.0}:
                        # Check connection health before sending command
                        if not self.connected:
                            logger.warning("Command timeout but connection lost - resetting velocity state locally")
                            self.velocity_controller.reset_velocity()
                            consecutive_failures += 1
                            continue
                        
                        if not self.connection_manager.connected:
                            logger.warning("Command timeout but connection manager reports disconnected")
                            self.velocity_controller.reset_velocity()
                            consecutive_failures += 1
                            continue
                        
                        # Connection is healthy, send zero velocity
                        success = await self.send_zero_velocity()
                        
                        if success:
                            consecutive_failures = 0
                        else:
                            consecutive_failures += 1
                            if consecutive_failures >= max_consecutive_failures:
                                logger.error(f"Command timeout handler: {consecutive_failures} consecutive failures")
                                
        except asyncio.CancelledError:
            logger.debug("Command timeout handler cancelled")
            raise
    
    def scale_velocity(self, normalized_value: float, max_value: float) -> float:
        """Scale normalized value (-1 to 1) to actual velocity"""
        return self.velocity_controller.scale_velocity(normalized_value, max_value)
    
    def get_velocity_limits(self) -> dict:
        """Get velocity limits"""
        return self.velocity_controller.get_velocity_limits()
    
    @property
    def offboard_initialized(self) -> bool:
        """Check if offboard mode is initialized."""
        return self.offboard_manager.offboard_initialized
    
    @property
    def offboard_active(self) -> bool:
        """Check if offboard mode is currently active."""
        return self.offboard_manager.offboard_active
    
    @property
    def failsafe_active(self) -> bool:
        """Check if failsafe is currently active."""
        return self.offboard_manager.failsafe_active
    
    @property
    def failsafe_recovery_attempts(self) -> int:
        """Get current failsafe recovery attempts count."""
        return self.offboard_manager.failsafe_recovery_attempts
    
    @property
    def shutdown_requested(self):
        """Get shutdown request event for graceful shutdown."""
        return self.background_tasks.shutdown_requested
    
    def set_shutdown_event(self, shutdown_event):
        """Set the shutdown event for early shutdown detection in connection manager."""
        self.connection_manager.set_shutdown_event(shutdown_event)
    
    @property
    def gps_healthy(self) -> bool:
        """Check if GPS is healthy."""
        return self.connection_manager.gps_healthy
    
    @property
    def gps_info(self) -> dict:
        """Get GPS info (fix type, satellites)."""
        return {
            "healthy": self.connection_manager.gps_healthy,
            "fix_type": self.connection_manager.gps_fix_type,
            "num_satellites": self.connection_manager.gps_num_satellites
        }
    
    @property
    def px4_failsafe_active(self) -> bool:
        """Check if PX4 failsafe is active."""
        return self.background_tasks.px4_failsafe_triggered
    
    @property
    def px4_failsafe_type(self) -> Optional[str]:
        """Get PX4 failsafe type if active."""
        return self.background_tasks.px4_failsafe_type