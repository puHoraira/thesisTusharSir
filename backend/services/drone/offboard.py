"""
Offboard mode management for drone service.
"""

import asyncio
import logging
from typing import Optional, Tuple

import mavsdk

from config import (
    OFFBOARD_SETPOINT_RATE_HZ,
    OFFBOARD_INIT_SETPOINT_DURATION,
    OFFBOARD_INIT_RETRY_ATTEMPTS,
    OFFBOARD_SETPOINT_INTERVAL
)

logger = logging.getLogger("drone_offboard_manager")


class OffboardManager:
    """Manages offboard mode initialization and recovery."""
    
    def __init__(self, drone: Optional[mavsdk.System]):
        self.drone = drone
        self.offboard_initialized: bool = False
        self.offboard_active: bool = False
        self.failsafe_active = False
        self.failsafe_recovery_attempts = 0
        self.max_failsafe_recovery_attempts = 3
    
    def set_drone(self, drone: Optional[mavsdk.System]):
        """Update drone instance reference."""
        self.drone = drone
    
    async def initialize(self) -> Tuple[bool, str]:
        """
        Initialize offboard mode with proper sequence and verification.
        
        PX4 requires:
        - Setpoints streamed for >1 second before offboard.start()
        - Continuous setpoints at >2Hz to maintain offboard mode
        
        Returns:
            (success: bool, message: str)
        """
        if not self.drone:
            return False, "Drone not connected"
        
        if self.offboard_initialized:
            logger.info("Offboard mode already initialized")
            return True, "Offboard mode already active"
        
        # Safety check: stop any existing offboard tasks from previous flights
        # This ensures we start with a clean state
        self.offboard_active = False
        self.failsafe_active = False
        self.failsafe_recovery_attempts = 0
        
        logger.info(f"Initializing offboard mode (rate: {OFFBOARD_SETPOINT_RATE_HZ}Hz)...")
        
        # Retry logic with exponential backoff
        for attempt in range(OFFBOARD_INIT_RETRY_ATTEMPTS):
            try:
                if attempt > 0:
                    backoff_time = 2 ** attempt  # Exponential backoff: 2, 4, 8 seconds
                    logger.info(f"Retry attempt {attempt + 1}/{OFFBOARD_INIT_RETRY_ATTEMPTS} after {backoff_time}s...")
                    await asyncio.sleep(backoff_time)
                
                # Send initial setpoints before starting offboard (PX4 requirement: >1 second)
                num_setpoints = int(OFFBOARD_INIT_SETPOINT_DURATION * OFFBOARD_SETPOINT_RATE_HZ)
                logger.info(f"Sending {num_setpoints} initial setpoints over {OFFBOARD_INIT_SETPOINT_DURATION}s...")
                
                for i in range(num_setpoints):
                    await self.drone.offboard.set_velocity_body(
                        mavsdk.offboard.VelocityBodyYawspeed(0.0, 0.0, 0.0, 0.0)
                    )
                    await asyncio.sleep(OFFBOARD_SETPOINT_INTERVAL)
                
                # Start offboard mode
                logger.info("Starting offboard mode...")
                logger.info("📡 MAVLink DO_SET_MODE → Pixhawk | Command: 176 | Param1: 1 (CUSTOM_MODE) | Param2: 6 (OFFBOARD)")
                await self.drone.offboard.start()
                self.offboard_initialized = True
                self.offboard_active = True
                logger.info("Offboard mode started successfully")
                
                # Send additional setpoints to ensure mode switch completes
                logger.info("Sending additional setpoints to confirm mode switch...")
                for i in range(5):
                    await self.drone.offboard.set_velocity_body(
                        mavsdk.offboard.VelocityBodyYawspeed(0.0, 0.0, 0.0, 0.0)
                    )
                    await asyncio.sleep(OFFBOARD_SETPOINT_INTERVAL)
                
                # Verify flight mode changed to OFFBOARD
                try:
                    flight_mode = await asyncio.wait_for(
                        self.drone.telemetry.flight_mode().__anext__(), timeout=2.0
                    )
                    logger.info(f"Flight mode after offboard start: {flight_mode}")
                    
                    if "OFFBOARD" in str(flight_mode).upper() or "OFF_BOARD" in str(flight_mode).upper():
                        logger.info("Successfully verified OFFBOARD mode")
                        return True, "Offboard mode initialized"
                    else:
                        logger.warning(f"Flight mode is {flight_mode}, not OFFBOARD. Background streamer will maintain mode.")
                        # Still return success - background task will maintain offboard
                        return True, "Offboard mode started (background streamer active)"
                        
                except Exception as mode_check_error:
                    logger.warning(f"Could not verify flight mode: {mode_check_error}. Background streamer will maintain offboard.")
                    return True, "Offboard mode started (verification timeout)"
                
            except Exception as e:
                logger.error(f"Offboard initialization attempt {attempt + 1} failed: {e}")
                self.offboard_initialized = False
                self.offboard_active = False
                
                if attempt == OFFBOARD_INIT_RETRY_ATTEMPTS - 1:
                    return False, f"Failed to initialize offboard mode after {OFFBOARD_INIT_RETRY_ATTEMPTS} attempts: {str(e)}"
        
        return False, "Offboard initialization failed"
    
    async def resume(self) -> Tuple[bool, str]:
        """
        Resume offboard mode on a drone that's already in flight.
        
        This is specifically designed for reconnection scenarios where:
        - Backend restarted but PX4 stayed alive
        - Drone is in HOLD mode (or similar) in the air
        - Need to transition to OFFBOARD without landing
        
        Returns:
            (success: bool, message: str)
        """
        if not self.drone:
            return False, "Drone not connected"
        
        if self.offboard_initialized:
            logger.warning("Offboard already initialized, skipping resume")
            return True, "Already in offboard mode"
        
        logger.info("Resuming offboard mode for in-flight reconnection")
        
        # Stop any existing tasks to ensure clean state
        self.offboard_active = False
        self.failsafe_active = False
        self.failsafe_recovery_attempts = 0
        
        # Retry logic with exponential backoff (more aggressive for reconnection)
        retry_delays = [2.0, 3.0, 5.0, 8.0, 10.0]  # 5 attempts
        
        for attempt, delay in enumerate(retry_delays):
            try:
                if attempt > 0:
                    logger.info(f"Retry attempt {attempt + 1}/{len(retry_delays)} after {delay}s...")
                    await asyncio.sleep(delay)
                
                # Send many initial setpoints (40 over 4 seconds at 10Hz)
                # More setpoints = more reliable transition for in-flight resumption
                num_initial_setpoints = 40
                logger.info(f"Sending {num_initial_setpoints} initial setpoints for HOLD→OFFBOARD transition...")
                
                for i in range(num_initial_setpoints):
                    await self.drone.offboard.set_velocity_body(
                        mavsdk.offboard.VelocityBodyYawspeed(0.0, 0.0, 0.0, 0.0)
                    )
                    await asyncio.sleep(OFFBOARD_SETPOINT_INTERVAL)
                    
                    # Log progress every 10 setpoints
                    if (i + 1) % 10 == 0:
                        logger.debug(f"Sent {i + 1}/{num_initial_setpoints} setpoints")
                
                # Start offboard mode
                logger.info("Starting offboard mode (reconnection)...")
                await self.drone.offboard.start()
                
                # Send confirmation setpoints after start (20 more)
                logger.info("Sending confirmation setpoints...")
                for i in range(20):
                    await self.drone.offboard.set_velocity_body(
                        mavsdk.offboard.VelocityBodyYawspeed(0.0, 0.0, 0.0, 0.0)
                    )
                    await asyncio.sleep(OFFBOARD_SETPOINT_INTERVAL)
                
                # Set state flags
                self.offboard_initialized = True
                self.offboard_active = True
                
                # Verify mode switched
                try:
                    flight_mode = await asyncio.wait_for(
                        self.drone.telemetry.flight_mode().__anext__(), timeout=2.0
                    )
                    flight_mode_str = str(flight_mode).upper()
                    
                    if "OFFBOARD" in flight_mode_str or "OFF_BOARD" in flight_mode_str:
                        logger.info(f"Successfully resumed OFFBOARD mode: Flight mode {flight_mode}")
                        return True, "Offboard mode resumed successfully"
                    else:
                        logger.warning(f"Mode is {flight_mode}, not OFFBOARD. Background streamer will maintain.")
                        return True, f"Offboard started, current mode: {flight_mode}"
                        
                except Exception as mode_check_error:
                    logger.warning(f"Could not verify flight mode: {mode_check_error}")
                    return True, "Offboard resumed (verification timeout)"
                
            except Exception as e:
                logger.error(f"Resume attempt {attempt + 1} failed: {e}")
                self.offboard_initialized = False
                self.offboard_active = False
                
                if attempt == len(retry_delays) - 1:
                    return False, f"Failed to resume offboard after {len(retry_delays)} attempts: {str(e)}"
        
        return False, "Offboard resume failed"
    
    async def stop(self):
        """Stop offboard mode."""
        if self.drone and self.offboard_initialized:
            try:
                await self.drone.offboard.stop()
                logger.info("Offboard mode stopped")
            except Exception as e:
                logger.warning(f"Error stopping offboard mode: {e}")
        
        self.offboard_initialized = False
        self.offboard_active = False
        self.failsafe_active = False
        self.failsafe_recovery_attempts = 0
    
    def reset_state(self):
        """Reset offboard state flags."""
        self.offboard_initialized = False
        self.offboard_active = False
        self.failsafe_active = False
        self.failsafe_recovery_attempts = 0