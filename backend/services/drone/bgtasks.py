"""
Background tasks for drone service (setpoint streaming and monitoring).
"""

import asyncio
import logging
import threading
import time
from typing import Optional

import mavsdk

from config import (
    OFFBOARD_SETPOINT_RATE_HZ,
    OFFBOARD_SETPOINT_INTERVAL,
    OFFBOARD_MODE_CHECK_INTERVAL,
    MAX_VELOCITY_HORIZONTAL,
    MAX_VELOCITY_VERTICAL,
    MAX_YAW_RATE,
    SETPOINT_STREAMER_MAX_RESTARTS,
    SETPOINT_STREAMER_RESTART_BACKOFF,
    STATE_SYNC_INTERVAL
)
from .utils import sanitize_setpoint_value

logger = logging.getLogger("drone_background_tasks")


class BackgroundTasks:
    """Manages background tasks for offboard mode maintenance."""
    
    def __init__(self, drone: Optional[mavsdk.System], velocity_controller, offboard_manager):
        self.drone = drone
        self.velocity_controller = velocity_controller
        self.offboard_manager = offboard_manager
        
        # Background tasks
        self.offboard_setpoint_task: Optional[asyncio.Task] = None
        self.offboard_monitor_task: Optional[asyncio.Task] = None
        self.state_sync_task: Optional[asyncio.Task] = None
        
        # Statistics
        self.setpoint_count = 0
        self.last_setpoint_time: Optional[float] = None
        
        # Setpoint streamer restart tracking
        self.setpoint_streamer_restart_count: int = 0
        self.setpoint_streamer_last_restart: Optional[float] = None
        
        # PX4 failsafe status tracking
        self.px4_failsafe_triggered: bool = False
        self.px4_failsafe_type: Optional[str] = None
        
        # Safety manager reference (set by start_state_sync or externally)
        self._safety_manager = None
        
        # Graceful shutdown support
        self.shutdown_requested = threading.Event()
    
    def set_drone(self, drone: Optional[mavsdk.System]):
        """Update drone instance reference."""
        self.drone = drone
    
    def set_safety_manager(self, safety_manager):
        """Set safety manager reference for armed status checks."""
        self._safety_manager = safety_manager
    
    def start_setpoint_streamer(self):
        """Start background task to continuously stream setpoints to PX4 to maintain OFFBOARD mode"""
        if self.offboard_setpoint_task and not self.offboard_setpoint_task.done():
            logger.debug("Setpoint streamer already running")
            return
        
        async def setpoint_streamer_loop():
            """
            Continuously stream velocity setpoints at configured rate (default 10Hz).
            This is the ONLY place that sends setpoints to PX4.
            Includes auto-restart with exponential backoff.
            """
            logger.debug(f"Starting setpoint streamer at {OFFBOARD_SETPOINT_RATE_HZ}Hz")
            consecutive_errors = 0
            max_consecutive_errors = 5
            should_restart = False
            
            try:
                while (self.offboard_manager.offboard_initialized and 
                       self.drone and 
                       not self.shutdown_requested.is_set()):
                    
                    # Check for shutdown request
                    if self.shutdown_requested.is_set():
                        logger.debug("Shutdown requested, stopping setpoint streamer")
                        break
                    
                    try:
                        # Get current velocity (thread-safe)
                        current_velocity = self.velocity_controller.get_current_velocity()
                        vx_raw = current_velocity.get("vx", 0.0)
                        vy_raw = current_velocity.get("vy", 0.0)
                        vz_raw = current_velocity.get("vz", 0.0)
                        yaw_rate_raw = current_velocity.get("yaw_rate", 0.0)
                        
                        # Sanitize all values to ensure they're finite (PX4 requirement)
                        vx = sanitize_setpoint_value(vx_raw, 0.0)
                        vy = sanitize_setpoint_value(vy_raw, 0.0)
                        vz = sanitize_setpoint_value(vz_raw, 0.0)
                        yaw_rate = sanitize_setpoint_value(yaw_rate_raw, 0.0)
                        
                        # Additional validation: clamp to reasonable limits as safety measure
                        vx = max(-MAX_VELOCITY_HORIZONTAL, min(MAX_VELOCITY_HORIZONTAL, vx))
                        vy = max(-MAX_VELOCITY_HORIZONTAL, min(MAX_VELOCITY_HORIZONTAL, vy))
                        vz = max(-MAX_VELOCITY_VERTICAL, min(MAX_VELOCITY_VERTICAL, vz))
                        yaw_rate = max(-MAX_YAW_RATE, min(MAX_YAW_RATE, yaw_rate))
                        
                        # Log MAVLink packet details (only log non-zero commands to avoid spam)
                        if vx != 0 or vy != 0 or vz != 0 or yaw_rate != 0:
                            logger.info(
                                f"📡 MAVLink SET_POSITION_TARGET_LOCAL_NED → Pixhawk | "
                                f"Frame: BODY_NED (coordinate_frame=8) | "
                                f"Type_mask: 0b0000011111000111 (velocity control) | "
                                f"Velocity: vx={vx:.3f}m/s, vy={vy:.3f}m/s, vz={vz:.3f}m/s, yaw_rate={yaw_rate:.3f}°/s | "
                                f"Rate: {1/OFFBOARD_SETPOINT_INTERVAL:.1f}Hz"
                            )
                        
                        # Send setpoint to PX4
                        await self.drone.offboard.set_velocity_body(
                            mavsdk.offboard.VelocityBodyYawspeed(vx, vy, vz, yaw_rate)
                        )
                        
                        # Update statistics
                        self.setpoint_count += 1
                        current_time = asyncio.get_event_loop().time()
                        
                        if self.last_setpoint_time:
                            actual_interval = current_time - self.last_setpoint_time
                            # Log if interval deviates significantly (>20%)
                            if abs(actual_interval - OFFBOARD_SETPOINT_INTERVAL) > (OFFBOARD_SETPOINT_INTERVAL * 0.2):
                                logger.debug(f"Setpoint interval deviation: {actual_interval:.3f}s (target: {OFFBOARD_SETPOINT_INTERVAL:.3f}s)")
                        
                        self.last_setpoint_time = current_time
                        consecutive_errors = 0  # Reset error counter on success
                        
                        # Reset restart count on successful operation
                        if self.setpoint_streamer_restart_count > 0:
                            # If we've been running successfully for a while, reset the count
                            if self.setpoint_streamer_last_restart:
                                if time.time() - self.setpoint_streamer_last_restart > 30.0:
                                    self.setpoint_streamer_restart_count = 0
                        
                    except Exception as e:
                        consecutive_errors += 1
                        logger.warning(f"Setpoint streaming error ({consecutive_errors}/{max_consecutive_errors}): {e}")
                        
                        if consecutive_errors >= max_consecutive_errors:
                            logger.error(f"Too many consecutive setpoint errors ({consecutive_errors})")
                            self.offboard_manager.offboard_active = False
                            should_restart = True
                            break
                    
                    # Sleep for the configured interval
                    await asyncio.sleep(OFFBOARD_SETPOINT_INTERVAL)
                    
            except asyncio.CancelledError:
                logger.debug("Setpoint streamer cancelled")
                raise
            except Exception as e:
                logger.error(f"Setpoint streamer failed: {e}")
                self.offboard_manager.offboard_active = False
                should_restart = True
            finally:
                logger.debug(f"Setpoint streamer stopped. Total setpoints sent: {self.setpoint_count}")
                
                # Auto-restart logic with exponential backoff
                if (should_restart and 
                    self.offboard_manager.offboard_initialized and 
                    not self.shutdown_requested.is_set() and
                    self.setpoint_streamer_restart_count < SETPOINT_STREAMER_MAX_RESTARTS):
                    
                    self.setpoint_streamer_restart_count += 1
                    self.setpoint_streamer_last_restart = time.time()
                    
                    # Calculate backoff with exponential increase
                    backoff_time = SETPOINT_STREAMER_RESTART_BACKOFF * (2 ** (self.setpoint_streamer_restart_count - 1))
                    backoff_time = min(backoff_time, 30.0)  # Cap at 30 seconds
                    
                    logger.warning(f"Scheduling setpoint streamer restart ({self.setpoint_streamer_restart_count}/{SETPOINT_STREAMER_MAX_RESTARTS}) in {backoff_time:.1f}s")
                    
                    # Send zero velocity immediately to prevent mode exit
                    try:
                        if self.drone:
                            await self.drone.offboard.set_velocity_body(
                                mavsdk.offboard.VelocityBodyYawspeed(0.0, 0.0, 0.0, 0.0)
                            )
                            logger.debug("Sent zero velocity before restart")
                    except Exception as e:
                        logger.error(f"Failed to send zero velocity before restart: {e}")
                    
                    await asyncio.sleep(backoff_time)
                    
                    if not self.shutdown_requested.is_set() and self.offboard_manager.offboard_initialized:
                        logger.debug("Restarting setpoint streamer")
                        self.start_setpoint_streamer()
                elif should_restart and self.setpoint_streamer_restart_count >= SETPOINT_STREAMER_MAX_RESTARTS:
                    logger.error(f"Setpoint streamer max restarts ({SETPOINT_STREAMER_MAX_RESTARTS}) exceeded - not restarting")
        
        self.offboard_setpoint_task = asyncio.create_task(setpoint_streamer_loop())
        logger.debug("Setpoint streamer started")
    
    def start_offboard_monitor(self):
        """Start background task to monitor offboard mode health and PX4 failsafe status"""
        if self.offboard_monitor_task and not self.offboard_monitor_task.done():
            logger.debug("Offboard monitor already running")
            return
        
        async def monitor_loop():
            """
            Monitor offboard mode status using a single subscription.
            Uses async for to avoid creating new subscriptions each iteration.
            Gets armed status from safety_manager (updated by telemetry streams).
            """
            logger.debug("Starting offboard mode health monitor")
            
            try:
                if not self.drone:
                    return
                
                # Create subscription ONCE, then iterate
                async for flight_mode in self.drone.telemetry.flight_mode():
                    if self.shutdown_requested.is_set():
                        logger.debug("Shutdown requested, stopping offboard monitor")
                        break
                    
                    if not self.offboard_manager.offboard_initialized:
                        break
                    
                    # Get armed status from safety_manager (updated by telemetry streams)
                    is_armed = self._safety_manager.is_armed if self._safety_manager else False
                    
                    flight_mode_str = str(flight_mode).upper()
                    is_offboard = "OFFBOARD" in flight_mode_str or "OFF_BOARD" in flight_mode_str
                    
                    # Check for PX4 failsafe modes
                    await self._check_px4_failsafe(flight_mode_str)
                    
                    # Detect failsafe: not in offboard but we're trying to be
                    if not is_offboard and self.offboard_manager.offboard_initialized and is_armed:
                        if not self.offboard_manager.failsafe_active:
                            logger.warning(f"Failsafe detected: Flight mode {flight_mode} (expected OFFBOARD)")
                            self.offboard_manager.failsafe_active = True
                            self.offboard_manager.failsafe_recovery_attempts = 0
                        
                        # Attempt recovery: re-initialize offboard mode
                        if (self.offboard_manager.failsafe_active and 
                            self.offboard_manager.failsafe_recovery_attempts < self.offboard_manager.max_failsafe_recovery_attempts):
                            self.offboard_manager.failsafe_recovery_attempts += 1
                            logger.info(f"Offboard recovery attempt {self.offboard_manager.failsafe_recovery_attempts}/{self.offboard_manager.max_failsafe_recovery_attempts}")
                            
                            try:
                                # Stop current offboard and tasks
                                try:
                                    await self.drone.offboard.stop()
                                    await asyncio.sleep(0.5)
                                except:
                                    pass
                                
                                # Stop existing tasks
                                self.stop_all_tasks()
                                
                                # Reset state to allow re-initialization
                                self.offboard_manager.reset_state()
                                await asyncio.sleep(0.5)
                                
                                # Re-initialize offboard
                                success, message = await self.offboard_manager.initialize()
                                if success:
                                    logger.info(f"Offboard recovery successful")
                                    self.offboard_manager.failsafe_active = False
                                    self.offboard_manager.failsafe_recovery_attempts = 0
                                    # Restart background tasks
                                    self.start_setpoint_streamer()
                                    self.start_offboard_monitor()
                                    return  # Exit this monitor, new one started
                                else:
                                    logger.warning(f"Offboard recovery attempt {self.offboard_manager.failsafe_recovery_attempts} failed: {message}")
                                    
                            except Exception as recovery_error:
                                logger.error(f"Offboard recovery error: {recovery_error}")
                        
                        self.offboard_manager.offboard_active = False
                        
                    elif is_offboard:
                        # Successfully in offboard mode
                        if self.offboard_manager.failsafe_active:
                            logger.info(f"Failsafe cleared - back in OFFBOARD mode")
                            self.offboard_manager.failsafe_active = False
                            self.offboard_manager.failsafe_recovery_attempts = 0
                        
                        if not self.offboard_manager.offboard_active:
                            logger.debug(f"Offboard mode active: {flight_mode}")
                            self.offboard_manager.offboard_active = True
                        
                        # Clear PX4 failsafe if we're back in offboard
                        if self.px4_failsafe_triggered:
                            logger.debug("PX4 failsafe cleared - back in OFFBOARD mode")
                            self.px4_failsafe_triggered = False
                            self.px4_failsafe_type = None
                        
            except asyncio.CancelledError:
                logger.debug("Offboard monitor cancelled")
                raise
            except Exception as e:
                logger.error(f"Offboard monitor failed: {e}")
        
        self.offboard_monitor_task = asyncio.create_task(monitor_loop())
        logger.debug("Offboard monitor started")
    
    async def _check_px4_failsafe(self, flight_mode_str: str):
        """Check for PX4 failsafe conditions based on flight mode and status."""
        failsafe_modes = ["RTL", "RETURN", "LAND", "DESCEND", "TERMINATE", "LOCKDOWN"]
        
        # Check if current mode indicates failsafe
        current_failsafe = None
        for mode in failsafe_modes:
            if mode in flight_mode_str:
                current_failsafe = mode
                break
        
        if current_failsafe and not self.px4_failsafe_triggered:
            self.px4_failsafe_triggered = True
            self.px4_failsafe_type = current_failsafe
            logger.warning(f"PX4 failsafe detected: {current_failsafe} mode triggered")
            
            # Try to get more info from status text
            try:
                if self.drone:
                    status = await asyncio.wait_for(
                        self.drone.telemetry.status_text().__anext__(),
                        timeout=0.5
                    )
                    logger.warning(f"PX4 status: {status.text}")
            except (asyncio.TimeoutError, Exception):
                pass  # Status text not always available
    
    def start_state_sync(self, safety_manager):
        """Start background task to periodically synchronize backend state with PX4."""
        if self.state_sync_task and not self.state_sync_task.done():
            logger.debug("State sync already running")
            return
        
        async def state_sync_loop():
            """Periodically verify and sync backend state with PX4 actual state."""
            logger.debug("Starting state synchronization monitor")
            self._safety_manager = safety_manager
            
            try:
                while (self.drone and 
                       not self.shutdown_requested.is_set()):
                    await asyncio.sleep(STATE_SYNC_INTERVAL)
                    
                    if self.shutdown_requested.is_set():
                        logger.debug("Shutdown requested, stopping state sync")
                        break
                    
                    try:
                        await self._sync_state()
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.debug(f"State sync error: {e}")
                        
            except asyncio.CancelledError:
                logger.debug("State sync cancelled")
                raise
            except Exception as e:
                logger.error(f"State sync failed: {e}")
            finally:
                logger.debug("State sync stopped")
        
        self.state_sync_task = asyncio.create_task(state_sync_loop())
        logger.debug("State sync started")
    
    async def _sync_state(self):
        """
        Synchronize backend state with PX4 actual state.
        
        Checks:
        - Armed status
        - Flight mode
        - Offboard initialization status
        """
        if not self.drone:
            return
        
        corrections_made = []
        
        try:
            # Sync armed status
            px4_armed = await asyncio.wait_for(
                self.drone.telemetry.armed().__anext__(),
                timeout=1.0
            )
            
            if hasattr(self, '_safety_manager') and self._safety_manager:
                if self._safety_manager.is_armed != px4_armed:
                    logger.warning(f"State sync: Armed status mismatch - backend={self._safety_manager.is_armed}, PX4={px4_armed}")
                    self._safety_manager.is_armed = px4_armed
                    corrections_made.append("armed_status")
            
            # Sync flight mode
            px4_flight_mode = await asyncio.wait_for(
                self.drone.telemetry.flight_mode().__anext__(),
                timeout=1.0
            )
            
            flight_mode_str = str(px4_flight_mode).upper()
            is_px4_offboard = "OFFBOARD" in flight_mode_str or "OFF_BOARD" in flight_mode_str
            
            # Check offboard state consistency
            if self.offboard_manager.offboard_initialized and not is_px4_offboard and px4_armed:
                # Backend thinks we're in offboard, but PX4 is not
                if not self.offboard_manager.failsafe_active:
                    logger.warning(f"State sync: Offboard mismatch - backend=initialized, PX4={flight_mode_str}")
                    # Don't auto-correct - this is handled by offboard monitor
                    # Just log for awareness
            elif not self.offboard_manager.offboard_initialized and is_px4_offboard:
                # PX4 is in offboard but backend doesn't know
                logger.warning(f"State sync: Offboard mismatch - backend=not_initialized, PX4=OFFBOARD")
                # This can happen after reconnection - flag for recovery
                corrections_made.append("offboard_detection")
            
            # Sync offboard active status
            if is_px4_offboard and not self.offboard_manager.offboard_active:
                self.offboard_manager.offboard_active = True
                corrections_made.append("offboard_active")
            elif not is_px4_offboard and self.offboard_manager.offboard_active and px4_armed:
                self.offboard_manager.offboard_active = False
                corrections_made.append("offboard_inactive")
            
            if corrections_made:
                logger.debug(f"State sync completed with corrections: {', '.join(corrections_made)}")
            else:
                logger.debug("State sync completed - no corrections needed")
                
        except asyncio.TimeoutError:
            logger.debug("State sync timeout - telemetry unavailable")
        except Exception as e:
            logger.debug(f"State sync check error: {e}")
    
    def stop_all_tasks(self):
        """Stop all background tasks gracefully"""
        # Signal shutdown request first (gives tasks a chance to exit cleanly)
        self.shutdown_requested.set()
        
        tasks_stopped = []
        
        if self.offboard_setpoint_task and not self.offboard_setpoint_task.done():
            self.offboard_setpoint_task.cancel()
            tasks_stopped.append("setpoint streamer")
        
        if self.offboard_monitor_task and not self.offboard_monitor_task.done():
            self.offboard_monitor_task.cancel()
            tasks_stopped.append("monitor")
        
        if self.state_sync_task and not self.state_sync_task.done():
            self.state_sync_task.cancel()
            tasks_stopped.append("state sync")
        
        if tasks_stopped:
            logger.debug(f"Stopped offboard tasks: {', '.join(tasks_stopped)}")
        
        self.offboard_manager.offboard_active = False
        
        # Reset restart counts
        self.setpoint_streamer_restart_count = 0
        
        # Reset shutdown flag for future operations
        self.shutdown_requested.clear()
    
    def reset_restart_counts(self):
        """Reset all restart counters."""
        self.setpoint_streamer_restart_count = 0
        self.setpoint_streamer_last_restart = None
