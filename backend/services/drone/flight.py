"""
Flight operations (takeoff, landing, emergency stop) for drone service.
"""

import asyncio
import logging
import math
import time
from typing import Optional, Tuple

import grpc
import mavsdk

from safety import SafetyManager
from config import (
    DEFAULT_TAKEOFF_ALTITUDE,
    MIN_TAKEOFF_ALTITUDE,
    TAKEOFF_OFFBOARD_DELAY,
    HARDWARE_TIMEOUT_MULTIPLIER
)

logger = logging.getLogger("drone_flight_ops")


class FlightOperations:
    """Manages flight operations like takeoff, landing, and emergency stop."""
    
    def __init__(self, drone: Optional["mavsdk.System"], safety_manager: SafetyManager, offboard_manager):
        self.drone = drone
        self.safety_manager = safety_manager
        self.offboard_manager = offboard_manager
    
    def set_drone(self, drone: Optional["mavsdk.System"]):
        """Update drone instance reference."""
        self.drone = drone
    
    async def takeoff(self, altitude: float = DEFAULT_TAKEOFF_ALTITUDE) -> Tuple[bool, str]:
        """
        Arm and takeoff drone
        
        Args:
            altitude: Target altitude in meters
        
        Returns:
            (success: bool, message: str)
        """
        if not self.drone:
            return False, "Drone not connected"
        
        try:
            # Check if already armed (get first value with timeout)
            is_already_armed = False
            try:
                is_already_armed = await asyncio.wait_for(
                    self.drone.telemetry.armed().__anext__(), timeout=1.0
                )
                if is_already_armed:
                    logger.info("Drone already armed, will use existing armed state")
            except (asyncio.TimeoutError, StopAsyncIteration):
                pass  # Not armed or timeout, continue to arming
            
            # Check system health before arming (only if not already armed)
            if not is_already_armed:
                max_health_retries = 2  # Reduced from 3 to avoid connection spam
                health_retry_delay = 0.5  # Reduced from 1.0
                
                for health_attempt in range(max_health_retries):
                    try:
                        health = await asyncio.wait_for(
                            self.drone.telemetry.health().__anext__(), timeout=3.0  # Increased from 2.0
                        )
                        # Log health check result only if it fails
                        logger.debug(f"Health check (attempt {health_attempt + 1}): armable={health.is_armable}")
                        if health.is_armable:
                            break  
                        
                        health_issues = []
                        if not health.is_gyrometer_calibration_ok:
                            health_issues.append("Gyrometer not calibrated")
                        if not health.is_accelerometer_calibration_ok:
                            health_issues.append("Accelerometer not calibrated")
                        if not health.is_magnetometer_calibration_ok:
                            health_issues.append("Magnetometer not calibrated")
                        if not health.is_local_position_ok:
                            health_issues.append("No local position")
                        if not health.is_global_position_ok:
                            health_issues.append("No global position (GPS)")
                        if not health.is_home_position_ok:
                            health_issues.append("No home position")
                        
                        if health_attempt < max_health_retries - 1:
                            # Retry after delay (might be transient during reconnection)
                            logger.debug(f"Health check failed, retrying in {health_retry_delay}s...")
                            await asyncio.sleep(health_retry_delay)
                        else:
                            if not health_issues:
                                logger.warning("All health checks pass but armable=False, attempting arm anyway")
                                break  
                            # Don't fail on GPS - common indoors, will just warn
                            if health_issues == ["No global position (GPS)"]:
                                logger.warning("GPS not available (indoor testing?) - will attempt arm anyway")
                                break
                            error_msg = f"Pre-arm check failed: {', '.join(health_issues)}"
                            logger.error(error_msg)
                            return False, error_msg
                            
                    except (asyncio.TimeoutError, StopAsyncIteration):
                        if health_attempt < max_health_retries - 1:
                            logger.warning(f"Health check timeout (attempt {health_attempt + 1}), retrying...")
                            await asyncio.sleep(health_retry_delay)
                        else:
                            logger.warning("Could not get health status after retries, attempting to arm anyway")
                
                # Arm the drone
                logger.debug("Arming drone...")
                logger.info("📡 MAVLink COMPONENT_ARM_DISARM → Pixhawk | Param1: 1.0 (ARM)")
                await self.drone.action.arm()
                await asyncio.sleep(0.5)
                
                # Verify armed state (get first value with timeout)
                try:
                    is_armed = await asyncio.wait_for(
                        self.drone.telemetry.armed().__anext__(), timeout=1.0
                    )
                    if not is_armed:
                        return False, "Failed to arm drone - PX4 rejected arm command. Check system health."
                except (asyncio.TimeoutError, StopAsyncIteration):
                    logger.warning("Timeout checking armed state, proceeding with takeoff")
            
            # Determine requested takeoff altitude within safety limits
            requested_altitude = altitude if altitude is not None else DEFAULT_TAKEOFF_ALTITUDE
            requested_altitude = max(requested_altitude, MIN_TAKEOFF_ALTITUDE)
            is_safe_altitude, capped_altitude = self.safety_manager.check_altitude(requested_altitude)
            takeoff_altitude = capped_altitude

            if not is_safe_altitude:
                logger.warning(
                    f"Requested takeoff altitude {requested_altitude:.1f}m exceeds safety limit. "
                    f"Using capped altitude {takeoff_altitude:.1f}m."
                )

            # Configure PX4 takeoff altitude (PX4 defaults to MIS_TAKEOFF_ALT ~2.5m if not set)
            try:
                await self.drone.action.set_takeoff_altitude(takeoff_altitude)
                logger.debug(f"Set takeoff altitude to {takeoff_altitude:.1f}m")
            except Exception as set_alt_error:
                logger.warning(f"Could not set takeoff altitude: {set_alt_error}. Proceeding with PX4 default.")

            # Takeoff
            logger.info(f"📡 MAVLink NAV_TAKEOFF → Pixhawk | Altitude: {takeoff_altitude:.1f}m")
            await self.drone.action.takeoff()
            logger.info("Takeoff initiated")
            
            # Wait for drone to reach target altitude with improved verification
            target_reached = False
            target_altitude_threshold = takeoff_altitude * 0.9  # consider reached at 90% of requested altitude
            base_timeout = max(15.0, takeoff_altitude * 4.0)  # increased timeout for reliability
            takeoff_timeout = base_timeout * HARDWARE_TIMEOUT_MULTIPLIER  # 50% longer for hardware
            start_time = time.time()
            
            # Track consecutive valid readings for stability verification
            consecutive_valid_readings = 0
            required_valid_readings = 3  # Need 3 consecutive readings above threshold
            last_logged_altitude = -1.0

            while time.time() - start_time < takeoff_timeout:
                try:
                    position = await asyncio.wait_for(self.drone.telemetry.position().__anext__(), timeout=1.0)

                    relative_altitude = getattr(position, "relative_altitude_m", None)
                    absolute_altitude = getattr(position, "absolute_altitude_m", None)

                    if relative_altitude is None or not math.isfinite(relative_altitude):
                        # Fallback: compute from absolute altitude and home altitude
                        if absolute_altitude is not None and math.isfinite(absolute_altitude) and self.safety_manager.home_altitude:
                            relative_altitude = absolute_altitude - self.safety_manager.home_altitude

                    if relative_altitude is not None and math.isfinite(relative_altitude):
                        # Log altitude progress periodically (every 0.5m)
                        if abs(relative_altitude - last_logged_altitude) >= 0.5:
                            logger.debug(f"Climbing: {relative_altitude:.2f}m / {takeoff_altitude:.2f}m target")
                            last_logged_altitude = relative_altitude
                        
                        if relative_altitude >= target_altitude_threshold:
                            consecutive_valid_readings += 1
                            logger.debug(f"Altitude check {consecutive_valid_readings}/{required_valid_readings}: {relative_altitude:.2f}m")
                            
                            if consecutive_valid_readings >= required_valid_readings:
                                target_reached = True
                                logger.info(f"Takeoff altitude reached and stable: {relative_altitude:.2f}m")
                                break
                        else:
                            # Reset counter if altitude drops below threshold
                            consecutive_valid_readings = 0
                    else:
                        # Invalid reading, reset counter
                        consecutive_valid_readings = 0
                        
                    await asyncio.sleep(0.2)  # Check 5 times per second
                    
                except asyncio.TimeoutError:
                    consecutive_valid_readings = 0
                    continue
                except Exception as telemetry_error:
                    logger.debug(f"Error while monitoring takeoff altitude: {telemetry_error}")
                    consecutive_valid_readings = 0
                    await asyncio.sleep(0.5)

            if not target_reached:
                logger.warning(
                    f"Takeoff altitude threshold ({target_altitude_threshold:.1f}m) not reached within timeout "
                    f"({takeoff_timeout:.1f}s). Proceeding with current altitude."
                )
            
            # Mandatory delay before offboard initialization to ensure stability
            logger.debug(f"Waiting {TAKEOFF_OFFBOARD_DELAY:.1f}s before offboard initialization...")
            await asyncio.sleep(TAKEOFF_OFFBOARD_DELAY)
            
            # After takeoff, initialize OFFBOARD mode for velocity control
            try:
                success, message = await self.offboard_manager.initialize()
                if not success:
                    logger.warning(f"Offboard initialization failed: {message}")
                    # Don't fail takeoff - user can still control via other modes
            except Exception as offboard_error:
                logger.error(f"Could not initialize OFFBOARD mode after takeoff: {offboard_error}")
                self.offboard_manager.reset_state()
            
            return True, "Takeoff initiated"
            
        except Exception as e:
            logger.error(f"Takeoff failed: {e}")
            # Provide more helpful error messages
            error_str = str(e)
            if "COMMAND_DENIED" in error_str:
                return False, "Takeoff denied - drone may not be ready. Check pre-flight checks and ensure drone is in a safe state."
            return False, f"Takeoff failed: {error_str}"
    
    async def land(self) -> Tuple[bool, str]:
        """
        Land the drone and clean up offboard mode
        
        Returns:
            (success: bool, message: str)
        """
        if not self.drone:
            return False, "Drone not connected"
        
        try:
            # Stop offboard mode and tasks before landing
            if self.offboard_manager.offboard_initialized:
                logger.debug("Stopping offboard mode for landing...")
                try:
                    await self.offboard_manager.stop()
                except Exception as offboard_error:
                    logger.warning(f"Error stopping offboard mode: {offboard_error}")
            
            # Initiate landing
            await self.drone.action.land()
            logger.info("Landing initiated")
            return True, "Landing initiated"
        except Exception as e:
            logger.error(f"Land failed: {e}")
            return False, str(e)
    
    async def emergency_stop(self) -> Tuple[bool, str]:
        """
        Emergency stop - immediately stop offboard and initiate landing.
        PX4 will automatically disarm after landing.
        
        Returns:
            (success: bool, message: str)
        """
        if not self.drone:
            return False, "Drone not connected"
        
        try:
            # Stop offboard mode and tasks immediately
            if self.offboard_manager.offboard_initialized:
                logger.warning("Emergency stop - stopping offboard mode...")
                try:
                    await self.offboard_manager.stop()
                except Exception as offboard_error:
                    logger.warning(f"Error stopping offboard during emergency: {offboard_error}")
            
            # Initiate landing - PX4 will automatically disarm after landing
            # Attempting to disarm while in air will be denied by PX4
            await self.drone.action.land()
            logger.warning("Emergency stop - landing initiated (will disarm automatically after landing)")
            return True, "Emergency stop - landing initiated"
        except Exception as e:
            logger.error(f"Emergency stop failed: {e}")
            return False, str(e)
    
    async def detect_and_recover_flight_state(self) -> Tuple[bool, str]:
        """
        Detect if drone is already in flight (reconnection scenario) and attempt recovery.
        
        This is called after backend reconnects to PX4 that stayed alive.
        Checks if drone is armed and in the air, then attempts to resume offboard control.
        
        Enhanced with:
        - Multiple state verification attempts
        - Stale state detection with timestamps
        - Altitude-based recovery strategies
        - Partial connection handling
        
        Returns:
            (recovery_attempted: bool, message: str)
        """
        if not self.drone:
            return False, "Drone not connected"
        
        try:
            # After a reconnect event, telemetry streams can lag briefly.
            # Give MAVSDK a moment to deliver fresh samples before verification.
            await asyncio.sleep(0.75 * HARDWARE_TIMEOUT_MULTIPLIER)

            # Fast pre-check to avoid expensive recovery probing when the vehicle
            # is clearly not in flight (common during ground startup/no-GPS cases).
            try:
                is_armed_now = await asyncio.wait_for(
                    self.drone.telemetry.armed().__anext__(),
                    timeout=1.5 * HARDWARE_TIMEOUT_MULTIPLIER,
                )
                in_air_now = await asyncio.wait_for(
                    self.drone.telemetry.in_air().__anext__(),
                    timeout=1.5 * HARDWARE_TIMEOUT_MULTIPLIER,
                )

                if not is_armed_now or not in_air_now:
                    logger.debug(
                        f"No in-flight recovery needed (armed={is_armed_now}, in_air={in_air_now})"
                    )
                    return False, "Drone not airborne"
            except asyncio.TimeoutError:
                # If pre-check is unavailable, continue with deeper verification.
                logger.debug("Recovery pre-check timed out; continuing with full verification")

            # Step 1: Multiple verification attempts to ensure we have fresh data
            verification_attempts = 5
            verified_armed = False
            verified_altitude = None
            verified_flight_mode = None
            
            logger.debug("Starting flight state detection...")
            
            for attempt in range(verification_attempts):
                try:
                    # Check if drone is armed
                    is_armed = await asyncio.wait_for(
                        self.drone.telemetry.armed().__anext__(), 
                        timeout=2.5 * HARDWARE_TIMEOUT_MULTIPLIER
                    )
                    
                    # Check current flight mode
                    flight_mode = await asyncio.wait_for(
                        self.drone.telemetry.flight_mode().__anext__(), 
                        timeout=2.5 * HARDWARE_TIMEOUT_MULTIPLIER
                    )
                    
                    # Check altitude
                    position = await asyncio.wait_for(
                        self.drone.telemetry.position().__anext__(), 
                        timeout=2.5 * HARDWARE_TIMEOUT_MULTIPLIER
                    )
                    
                    relative_altitude = getattr(position, "relative_altitude_m", None)
                    
                    # Validate data freshness - check if values are reasonable
                    if relative_altitude is not None and math.isfinite(relative_altitude):
                        if verified_altitude is None:
                            verified_altitude = relative_altitude
                            verified_armed = is_armed
                            verified_flight_mode = str(flight_mode).upper()
                        else:
                            # Check consistency between readings
                            altitude_diff = abs(relative_altitude - verified_altitude)
                            if altitude_diff > 1.0:  # More than 1m difference suggests stale data
                                logger.warning(f"Altitude inconsistency detected: {altitude_diff:.2f}m difference")
                                verified_altitude = relative_altitude  # Use latest
                            else:
                                # Consistent readings - update with average
                                verified_altitude = (verified_altitude + relative_altitude) / 2
                        
                        logger.debug(f"Verification attempt {attempt + 1}: armed={is_armed}, altitude={relative_altitude:.2f}m, mode={flight_mode}")
                    
                    await asyncio.sleep(0.4)  # Wait between verification attempts
                    
                except asyncio.TimeoutError:
                    logger.warning(f"Verification attempt {attempt + 1} timeout")
                    continue
            
            if verified_altitude is None:
                logger.warning("Could not verify flight state after multiple attempts")
                return False, "Could not verify flight state - telemetry unavailable"
            
            if not verified_armed:
                logger.debug("Drone not armed - no reconnection recovery needed")
                return False, "Drone not armed"
            
            # Step 2: Altitude-based recovery strategy
            if verified_altitude > 0.5:
                logger.warning(
                    f"RECONNECTION DETECTED: Drone already flying at {verified_altitude:.2f}m "
                    f"in {verified_flight_mode} mode"
                )
                
                # Safety check: altitude limits for recovery
                if verified_altitude > 500.0:
                    logger.error(
                        f"Altitude too high ({verified_altitude:.2f}m) for automatic recovery. "
                        f"Manual intervention required."
                    )
                    return False, f"Altitude too high for recovery: {verified_altitude:.2f}m"
                
                # Low altitude warning (< 2m) - be extra careful
                if verified_altitude < 2.0:
                    logger.warning(f"Low altitude recovery ({verified_altitude:.2f}m) - using cautious approach")
                
                # Step 3: Mode-specific recovery
                if "OFFBOARD" in verified_flight_mode or "OFF_BOARD" in verified_flight_mode:
                    logger.info("Drone already in OFFBOARD mode, verifying and resuming control...")
                    
                    # Verify we can still communicate with offboard
                    try:
                        # Send a test setpoint
                        await asyncio.wait_for(
                            self.drone.offboard.set_velocity_body(
                                mavsdk.offboard.VelocityBodyYawspeed(0.0, 0.0, 0.0, 0.0)
                            ),
                            timeout=1.0
                        )
                        
                        # Set the state flags
                        self.offboard_manager.offboard_initialized = True
                        self.offboard_manager.offboard_active = True
                        
                        logger.info("Successfully verified OFFBOARD mode communication")
                        return True, "Resumed existing OFFBOARD mode"
                    except Exception as e:
                        logger.warning(f"OFFBOARD communication check failed: {e}")
                        # Try full resume
                        return await self._attempt_offboard_resume(verified_flight_mode, verified_altitude)
                
                elif "HOLD" in verified_flight_mode or "LOITER" in verified_flight_mode:
                    # Safe mode for recovery - drone is stable
                    logger.debug(f"Drone in {verified_flight_mode} mode - ideal for recovery")
                    return await self._attempt_offboard_resume(verified_flight_mode, verified_altitude)
                
                elif "RTL" in verified_flight_mode or "RETURN" in verified_flight_mode:
                    # RTL mode - consider if we should interrupt
                    logger.warning(f"Drone in RTL mode at {verified_altitude:.2f}m - evaluating recovery")
                    if verified_altitude > 10.0:
                        # High enough to safely attempt recovery
                        return await self._attempt_offboard_resume(verified_flight_mode, verified_altitude)
                    else:
                        # Let RTL complete at low altitude
                        logger.debug("Allowing RTL to complete at low altitude")
                        return False, "RTL in progress at low altitude"
                
                elif "LAND" in verified_flight_mode or "DESCEND" in verified_flight_mode:
                    # Landing mode - don't interrupt
                    logger.debug(f"Drone in {verified_flight_mode} mode - not interrupting landing")
                    return False, f"Landing in progress ({verified_flight_mode})"
                
                else:
                    # Unknown mode - attempt recovery with caution
                    logger.warning(f"Unknown flight mode: {verified_flight_mode} - attempting recovery")
                    return await self._attempt_offboard_resume(verified_flight_mode, verified_altitude)
            else:
                logger.debug(f"Drone armed but on ground (altitude: {verified_altitude:.2f}m)")
                return False, "Drone on ground"
                
        except asyncio.TimeoutError:
            logger.warning("Timeout while detecting flight state")
            return False, "Timeout detecting flight state"
        except grpc.aio.AioRpcError:
            # gRPC errors (like socket closed) are expected during shutdown
            logger.debug("Flight state detection stopped - connection closed")
            return False, "Connection closed"
        except Exception as e:
            logger.error(f"Error detecting flight state: {e}")
            return False, f"Error: {str(e)}"
    
    async def _attempt_offboard_resume(self, current_mode: str, altitude: float) -> Tuple[bool, str]:
        """
        Attempt to resume offboard mode with verification.
        
        Args:
            current_mode: Current flight mode string
            altitude: Current altitude in meters
            
        Returns:
            (success: bool, message: str)
        """
        logger.debug(f"Attempting to switch from {current_mode} to OFFBOARD at {altitude:.2f}m...")
        
        try:
            success, message = await self.offboard_manager.resume()
            
            if success:
                # Verify mode change took effect
                await asyncio.sleep(0.5)
                try:
                    new_mode = await asyncio.wait_for(
                        self.drone.telemetry.flight_mode().__anext__(),
                        timeout=2.0
                    )
                    new_mode_str = str(new_mode).upper()
                    
                    if "OFFBOARD" in new_mode_str or "OFF_BOARD" in new_mode_str:
                        logger.debug(f"Successfully transitioned to OFFBOARD mode")
                        return True, message
                    else:
                        logger.warning(f"Mode transition verification failed - now in {new_mode_str}")
                        return False, f"Mode transition failed - in {new_mode_str}"
                except asyncio.TimeoutError:
                    logger.warning("Could not verify mode transition")
                    return success, message
            else:
                return False, message
                
        except Exception as e:
            logger.error(f"Offboard resume attempt failed: {e}")
            return False, f"Resume failed: {str(e)}"
