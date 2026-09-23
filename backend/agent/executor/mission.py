"""
Mission Executor for waypoint-based navigation.
Handles waypoint queue management and execution with pause/resume support.
"""

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING
from threading import Lock

from agent.executor.waypoint import Waypoint, WaypointAction, WaypointStatus
from config import (
    WAYPOINT_POSITION_TOLERANCE,
    WAYPOINT_ALTITUDE_TOLERANCE,
    WAYPOINT_NAVIGATION_KP,
    MAX_VELOCITY_HORIZONTAL,
    MAX_VELOCITY_VERTICAL,
    MAXIMUM_WAYPOINT_TIMEOUT,
    METERS_PER_DEGREE_LAT,
    MAX_YAW_RATE,
)

if TYPE_CHECKING:
    from services.drone import DroneService
    from safety import SafetyManager

logger = logging.getLogger("mission_executor")


@dataclass
class MissionInfo:
    """Information about a mission."""
    mission_id: str
    waypoints: List[Waypoint]
    status: str = "pending"
    current_index: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    estimated_time: Optional[float] = None  # Estimated total duration in seconds


class MissionExecutor:
    """
    Executes waypoint-based missions.
    
    Manages:
    - Waypoint queue (thread-safe)
    - Waypoint navigation using velocity commands
    - Waypoint completion detection
    - Callbacks for waypoint completion events
    - Pause/Resume functionality for LLM evaluation
    """
    
    def __init__(
        self,
        drone_service: Optional["DroneService"] = None,
        safety_manager: Optional["SafetyManager"] = None,
    ):
        self.drone_service = drone_service
        self.safety_manager = safety_manager
        
        # Mission storage (mission_id -> MissionInfo)
        self._missions: Dict[str, MissionInfo] = {}
        self._missions_lock = Lock()
        
        # Current active mission
        self._active_mission_id: Optional[str] = None
        
        # Execution state
        self._executing = False
        self._paused = False  # Pause flag for LLM evaluation
        self._pause_event = asyncio.Event()  # Event to signal resume
        self._execution_task: Optional[asyncio.Task] = None
        
        # Callbacks
        self._on_waypoint_complete: Optional[Callable] = None
        self._on_mission_complete: Optional[Callable] = None
        self._on_mission_error: Optional[Callable] = None
        
        # Navigation parameters
        self._kp = WAYPOINT_NAVIGATION_KP
        self._max_velocity_h = MAX_VELOCITY_HORIZONTAL
        self._max_velocity_v = MAX_VELOCITY_VERTICAL
    
    def set_drone_service(self, drone_service: "DroneService"):
        """Set the drone service reference."""
        self.drone_service = drone_service
    
    def set_safety_manager(self, safety_manager: "SafetyManager"):
        """Set the safety manager reference."""
        self.safety_manager = safety_manager
    
    def set_callbacks(
        self,
        on_waypoint_complete: Optional[Callable] = None,
        on_mission_complete: Optional[Callable] = None,
        on_mission_error: Optional[Callable] = None,
    ):
        """Set callback functions for mission events."""
        self._on_waypoint_complete = on_waypoint_complete
        self._on_mission_complete = on_mission_complete
        self._on_mission_error = on_mission_error
    
    # ========== Waypoint Queue Management ==========
    
    def set_waypoints(self, mission_id: str, waypoints: List[Dict[str, Any]]):
        """
        Set waypoints for a mission.
        
        Args:
            mission_id: Unique mission identifier
            waypoints: List of waypoint dictionaries
        """
        with self._missions_lock:
            wp_objects = [Waypoint.from_dict(wp) for wp in waypoints]
            
            # Validate all waypoints
            for wp in wp_objects:
                is_valid, error = wp.validate()
                if not is_valid:
                    raise ValueError(f"Invalid waypoint {wp.waypoint_id}: {error}")
            
            self._missions[mission_id] = MissionInfo(
                mission_id=mission_id,
                waypoints=wp_objects,
                status="pending",
            )
            
            logger.info(f"Set {len(wp_objects)} waypoints for mission {mission_id}")
    
    def get_waypoints(self, mission_id: str) -> Optional[List[Dict[str, Any]]]:
        """Get waypoints for a mission."""
        with self._missions_lock:
            mission = self._missions.get(mission_id)
            if mission:
                return [wp.to_dict() for wp in mission.waypoints]
            return None
    
    def set_estimated_time(self, mission_id: str, estimated_time: float):
        """
        Set the estimated total duration for a mission.
        
        Args:
            mission_id: Unique mission identifier
            estimated_time: Estimated total duration in seconds
        """
        with self._missions_lock:
            mission = self._missions.get(mission_id)
            if mission:
                mission.estimated_time = estimated_time
                logger.info(f"Set estimated_time={estimated_time}s for mission {mission_id}")
            else:
                logger.warning(f"Cannot set estimated_time: mission {mission_id} not found")
    
    def get_mission_info(self, mission_id: str) -> Optional[Dict[str, Any]]:
        """Get full mission information."""
        with self._missions_lock:
            mission = self._missions.get(mission_id)
            if mission:
                return {
                    "mission_id": mission.mission_id,
                    "waypoints": [wp.to_dict() for wp in mission.waypoints],
                    "status": mission.status,
                    "current_index": mission.current_index,
                    "started_at": mission.started_at.isoformat() if mission.started_at else None,
                    "completed_at": mission.completed_at.isoformat() if mission.completed_at else None,
                    "error_message": mission.error_message,
                    "estimated_time": mission.estimated_time,
                }
            return None
    
    def clear_mission(self, mission_id: str):
        """Clear a mission from storage."""
        with self._missions_lock:
            if mission_id in self._missions:
                del self._missions[mission_id]
                logger.info(f"Cleared mission {mission_id}")
    
    def clear_all_missions(self):
        """Clear all missions from storage."""
        with self._missions_lock:
            count = len(self._missions)
            self._missions.clear()
            logger.info(f"Cleared all {count} missions from storage")
    
    # ========== Pause/Resume for LLM Evaluation ==========
    
    def pause_mission(self) -> bool:
        """
        Pause the current mission execution.
        Used when safety conditions trigger LLM evaluation.
        
        Returns:
            True if mission was paused, False if no mission is executing
        """
        if not self._executing or not self._active_mission_id:
            return False
        
        self._paused = True
        self._pause_event.clear()
        
        with self._missions_lock:
            mission = self._missions.get(self._active_mission_id)
            if mission:
                mission.status = "paused"
        
        # Send hover command to hold position while paused
        if self.drone_service:
            asyncio.create_task(self.drone_service.send_zero_velocity())
        
        logger.info(f"Mission {self._active_mission_id} paused for LLM evaluation")
        return True
    
    def resume_mission(self) -> bool:
        """
        Resume paused mission execution.
        Called after LLM evaluation completes.
        
        Returns:
            True if mission was resumed, False if no mission is paused
        """
        if not self._paused or not self._active_mission_id:
            return False
        
        self._paused = False
        self._pause_event.set()  # Signal the execution loop to continue
        
        with self._missions_lock:
            mission = self._missions.get(self._active_mission_id)
            if mission:
                mission.status = "executing"
        
        logger.info(f"Mission {self._active_mission_id} resumed")
        return True
    
    def update_remaining_waypoints(self, mission_id: str, new_waypoints: List[Dict[str, Any]]) -> bool:
        """
        Update remaining waypoints for a mission.
        Replaces waypoints from current_index onwards with new waypoints.
        
        Args:
            mission_id: Mission to update
            new_waypoints: New waypoints to replace remaining ones
            
        Returns:
            True if update was successful
        """
        with self._missions_lock:
            mission = self._missions.get(mission_id)
            if not mission:
                logger.error(f"Cannot update waypoints: Mission {mission_id} not found")
                return False
            
            # Convert new waypoints to Waypoint objects
            new_wp_objects = [Waypoint.from_dict(wp) for wp in new_waypoints]
            
            # Validate new waypoints
            for wp in new_wp_objects:
                is_valid, error = wp.validate()
                if not is_valid:
                    logger.error(f"Invalid new waypoint {wp.waypoint_id}: {error}")
                    return False
            
            # Keep completed waypoints, replace remaining
            completed_waypoints = mission.waypoints[:mission.current_index]
            mission.waypoints = completed_waypoints + new_wp_objects
            
            logger.info(f"Updated mission {mission_id}: kept {len(completed_waypoints)} completed, "
                       f"added {len(new_wp_objects)} new waypoints")
            return True
    
    @property
    def is_paused(self) -> bool:
        """Check if mission is currently paused."""
        return self._paused
    
    # ========== Mission Execution ==========
    
    async def start_mission(self, mission_id: str) -> tuple[bool, str]:
        """
        Start executing a mission.
        
        Args:
            mission_id: The mission to start
            
        Returns:
            Tuple of (success, message)
        """
        with self._missions_lock:
            mission = self._missions.get(mission_id)
            if not mission:
                return False, f"Mission {mission_id} not found"
            
            if self._executing:
                return False, "Another mission is already executing"
            
            mission.status = "executing"
            mission.started_at = datetime.now()
            mission.current_index = 0
            self._active_mission_id = mission_id
        
        self._executing = True
        self._paused = False
        self._pause_event.set()  # Start in non-paused state
        
        # Start execution task
        self._execution_task = asyncio.create_task(self._execute_mission_loop(mission_id))
        
        logger.info(f"Started mission {mission_id}")
        return True, f"Mission {mission_id} started"
    
    async def stop_mission(self, reason: str = "User requested") -> tuple[bool, str]:
        """
        Stop the current mission by landing the drone and marking the mission inactive.
        The execution loop exits on its next iteration when it sees _executing is False
        (no task cancellation, so safe to call from inside the loop e.g. eval abort).
        
        Args:
            reason: Reason for stopping
            
        Returns:
            Tuple of (success, message)
        """
        if not self._executing or not self._active_mission_id:
            return False, "No mission is currently executing"
        
        mission_id = self._active_mission_id
        
        # Mark mission inactive first so the loop will exit on its next iteration
        with self._missions_lock:
            mission = self._missions.get(mission_id)
            if mission:
                mission.status = "aborted"
                mission.error_message = reason
        
        self._executing = False
        self._active_mission_id = None
        
        # If paused, wake the loop so it can see _executing is False and exit
        if self._paused:
            self._paused = False
            self._pause_event.set()
        
        # Land the drone (stops offboard and initiates landing; no task cancel)
        if self.drone_service:
            success, msg = await self.drone_service.land()
            if not success:
                logger.warning(f"Land after stop_mission failed: {msg}")
        
        # Drop reference; the loop will exit naturally on next iteration
        self._execution_task = None
        
        logger.info(f"Stopped mission {mission_id}: {reason} (landing initiated)")
        return True, f"Mission stopped: {reason}"
    
    async def _execute_mission_loop(self, mission_id: str):
        """
        Main mission execution loop.
        Executes waypoints sequentially with pause/resume support.
        """
        try:
            while self._executing:
                # Wait if paused
                if self._paused:
                    logger.debug("Mission paused, waiting for resume...")
                    await self._pause_event.wait()
                    if not self._executing:  # Check if stopped while paused
                        break
                    continue
                
                with self._missions_lock:
                    mission = self._missions.get(mission_id)
                    if not mission:
                        break
                    
                    if mission.current_index >= len(mission.waypoints):
                        # Mission complete
                        mission.status = "completed"
                        mission.completed_at = datetime.now()
                        break
                    
                    current_wp = mission.waypoints[mission.current_index]
                
                # Execute current waypoint
                logger.info(f"Executing waypoint {current_wp.waypoint_id}: {current_wp.description}")
                
                current_wp.status = WaypointStatus.EXECUTING
                success, error = await self._execute_waypoint(current_wp)
                
                # Check if paused during waypoint execution
                if self._paused:
                    continue
                
                if success:
                    current_wp.status = WaypointStatus.COMPLETED
                    
                    # Update mission progress
                    with self._missions_lock:
                        mission = self._missions.get(mission_id)
                        if mission:
                            mission.current_index += 1
                    
                    # Call waypoint complete callback
                    if self._on_waypoint_complete:
                        try:
                            await self._on_waypoint_complete(current_wp.waypoint_id, mission_id)
                        except Exception as e:
                            logger.error(f"Waypoint complete callback error: {e}")
                    
                    # Check if paused by callback (for LLM evaluation)
                    if self._paused:
                        continue
                else:
                    current_wp.status = WaypointStatus.FAILED
                    raise Exception(f"Waypoint execution failed: {error}")
                
                # Small delay between waypoints
                await asyncio.sleep(0.1)
            
            # Loop ended: either all waypoints done or mission was aborted (stop_mission set _executing=False)
            self._executing = False
            self._active_mission_id = None
            
            with self._missions_lock:
                mission = self._missions.get(mission_id)
                status = mission.status if mission else "completed"
            
            if self._on_mission_complete:
                try:
                    await self._on_mission_complete(mission_id, status)
                except Exception as e:
                    logger.error(f"Mission complete callback error: {e}")
            
            if status == "aborted":
                logger.info(f"Mission {mission_id} ended (aborted)")
            else:
                logger.info(f"Mission {mission_id} completed successfully")
            
        except asyncio.CancelledError:
            logger.info(f"Mission {mission_id} execution cancelled")
            raise
        except Exception as e:
            logger.error(f"Mission {mission_id} execution error: {e}")
            
            with self._missions_lock:
                mission = self._missions.get(mission_id)
                if mission:
                    mission.status = "error"
                    mission.error_message = str(e)
            
            self._executing = False
            self._active_mission_id = None
            
            if self._on_mission_error:
                try:
                    await self._on_mission_error(mission_id, str(e))
                except Exception as cb_error:
                    logger.error(f"Mission error callback error: {cb_error}")
    
    async def _execute_waypoint(self, waypoint: Waypoint) -> tuple[bool, Optional[str]]:
        """
        Execute a single waypoint.
        
        Args:
            waypoint: The waypoint to execute
            
        Returns:
            Tuple of (success, error_message)
        """
        if not self.drone_service:
            return False, "Drone service not available"
        
        if not self.drone_service.connected:
            return False, "Drone not connected"
        
        action = waypoint.action
        
        if action == WaypointAction.TAKEOFF:
            altitude = waypoint.target_altitude or 2.5
            success, msg = await self.drone_service.takeoff(altitude=altitude)
            return success, msg if not success else None
        
        elif action == WaypointAction.LAND:
            success, msg = await self.drone_service.land()
            return success, msg if not success else None
        
        elif action == WaypointAction.HOVER:
            duration = waypoint.hover_duration or 5.0
            return await self._execute_hover(duration)
        
        elif action == WaypointAction.MOVE_TO:
            return await self._navigate_to_position(waypoint)
        
        elif action == WaypointAction.YAW:
            return await self._execute_yaw(waypoint)
        
        else:
            return False, f"Unknown action: {action}"
    
    async def _execute_hover(self, duration: float) -> tuple[bool, Optional[str]]:
        """Execute hover for specified duration."""
        logger.info(f"Hovering for {duration}s")
        
        start_time = time.time()
        
        while time.time() - start_time < duration:
            if not self._executing or self._paused:
                return False, "Execution stopped or paused"
            
            # Send zero velocity to maintain hover
            await self.drone_service.send_zero_velocity()
            await asyncio.sleep(0.1)
        
        return True, None
    
    async def _execute_yaw(self, waypoint: Waypoint) -> tuple[bool, Optional[str]]:
        """
        Execute yaw rotation waypoint.
        
        Args:
            waypoint: Waypoint with yaw parameters
            
        Returns:
            Tuple of (success, error_message)
        """
        yaw_rate = waypoint.yaw_rate
        duration = waypoint.yaw_duration
        direction = waypoint.yaw_direction or "right"
        
        if yaw_rate is None:
            return False, "yaw_rate not specified for yaw waypoint"
        if duration is None:
            return False, "yaw_duration not specified for yaw waypoint"
        
        # Validate yaw rate
        if abs(yaw_rate) > MAX_YAW_RATE:
            return False, f"yaw_rate ({yaw_rate}) exceeds maximum ({MAX_YAW_RATE})"
        
        # Apply direction
        if direction == "left":
            yaw_rate = -abs(yaw_rate)
        else:
            yaw_rate = abs(yaw_rate)
        
        total_angle = abs(yaw_rate) * duration
        logger.info(f"Executing yaw: {total_angle:.1f} degrees to the {direction}")
        
        start_time = time.time()
        remaining = duration
        
        while remaining > 0:
            if not self._executing or self._paused:
                await self.drone_service.send_zero_velocity()
                return False, "Execution stopped or paused"
            
            # Send yaw command (zero linear velocity, only yaw rate)
            success, msg = await self.drone_service.set_velocity_body(0.0, 0.0, 0.0, yaw_rate)
            if not success:
                logger.warning(f"Yaw velocity command failed: {msg}")
            
            await asyncio.sleep(0.1)
            elapsed = time.time() - start_time
            remaining = duration - elapsed
        
        # Stop yaw
        await self.drone_service.send_zero_velocity()
        
        actual_elapsed = time.time() - start_time
        actual_angle = abs(yaw_rate) * actual_elapsed
        logger.info(f"Yaw completed: {actual_angle:.1f} degrees in {actual_elapsed:.2f}s")
        
        return True, None
    
    async def _navigate_to_position(self, waypoint: Waypoint) -> tuple[bool, Optional[str]]:
        """
        Navigate to a position-based waypoint using velocity control.
        
        Uses proportional control to calculate velocity commands
        based on position error.
        """
        if not waypoint.target_lat or not waypoint.target_lon:
            return False, "Target position not specified"
        
        target_lat = waypoint.target_lat
        target_lon = waypoint.target_lon
        target_alt = waypoint.target_altitude or 10.0
        
        position_tolerance = waypoint.position_tolerance or WAYPOINT_POSITION_TOLERANCE
        altitude_tolerance = waypoint.altitude_tolerance or WAYPOINT_ALTITUDE_TOLERANCE
        
        timeout = MAXIMUM_WAYPOINT_TIMEOUT  # Maximum time to reach waypoint
        start_time = time.time()
        
        logger.info(
            f"🚁 [MISSION] NAVIGATING TO WAYPOINT:\n"
            f"   Waypoint ID: {waypoint.waypoint_id}\n"
            f"   Description: {waypoint.description}\n"
            f"   Target GPS: ({target_lat:.7f}, {target_lon:.7f})\n"
            f"   Target Altitude: {target_alt:.1f}m\n"
            f"   Tolerance: Position={position_tolerance}m, Altitude={altitude_tolerance}m"
        )
        
        while self._executing and not self._paused:
            if time.time() - start_time > timeout:
                return False, "Navigation timeout"
            
            # Get current position from safety manager
            if not self.safety_manager:
                return False, "Safety manager not available"
            
            # Use get_effective_position which handles stale data
            current_pos, is_stale = self.safety_manager.get_effective_position()
            if not current_pos or is_stale:
                await asyncio.sleep(0.1)
                continue
            
            # current_pos is a tuple of (lat, lon, alt)
            current_lat, current_lon, current_abs_alt = current_pos
            current_alt = self.safety_manager.current_relative_altitude or current_abs_alt
            
            if current_lat is None or current_lon is None:
                await asyncio.sleep(0.1)
                continue
            
            # Calculate position error
            error_lat = target_lat - current_lat
            error_lon = target_lon - current_lon
            error_alt = target_alt - current_alt
            
            # Convert to meters (approximate)
            error_north = error_lat * METERS_PER_DEGREE_LAT  # meters per degree lat
            error_east = error_lon * METERS_PER_DEGREE_LAT * math.cos(math.radians(current_lat))
            # Calculate horizontal distance
            horizontal_distance = math.sqrt(error_north**2 + error_east**2)

            # Check if waypoint reached
            if horizontal_distance < position_tolerance and abs(error_alt) < altitude_tolerance:
                logger.info(
                    f"✅ [MISSION] WAYPOINT REACHED:\n"
                    f"   Waypoint ID: {waypoint.waypoint_id}\n"
                    f"   Description: {waypoint.description}\n"
                    f"   Final Position: ({current_lat:.7f}, {current_lon:.7f}, {current_alt:.1f}m)\n"
                    f"   Target Position: ({target_lat:.7f}, {target_lon:.7f}, {target_alt:.1f}m)\n"
                    f"   Distance Error: {horizontal_distance:.2f}m, Altitude Error: {error_alt:.2f}m\n"
                    f"   Time Taken: {time.time() - start_time:.1f}s"
                )
                
                # Stop at waypoint
                await self.drone_service.send_zero_velocity()
                return True, None
            
            # Calculate velocity commands using proportional control in NED frame
            vel_north = self._kp * error_north
            vel_east = self._kp * error_east
            vz = -self._kp * error_alt   # Down (negative = up)
            
            # Transform NED velocities to body frame using current heading
            # Body frame: vx = forward, vy = right
            # NED frame: north = +X, east = +Y
            # Transform: vx_body = vel_north * cos(heading) + vel_east * sin(heading)
            #            vy_body = -vel_north * sin(heading) + vel_east * cos(heading)
            heading_deg, heading_stale = self.safety_manager.get_heading()
            
            # If heading is stale, hover and wait for fresh data
            if heading_stale:
                logger.warning("Heading data stale - hovering until fresh data available")
                await self.drone_service.send_zero_velocity()
                await asyncio.sleep(0.1)
                continue
            
            heading_rad = math.radians(heading_deg)
            
            cos_h = math.cos(heading_rad)
            sin_h = math.sin(heading_rad)
            
            # Rotate NED to body frame
            vx = vel_north * cos_h + vel_east * sin_h   # Forward
            vy = -vel_north * sin_h + vel_east * cos_h  # Right
            
            # Clamp velocities
            vx = self._clamp(vx, -self._max_velocity_h, self._max_velocity_h)
            vy = self._clamp(vy, -self._max_velocity_h, self._max_velocity_h)
            vz = self._clamp(vz, -self._max_velocity_v, self._max_velocity_v)
            
            # Send velocity command in body frame
            logger.info(
                f"🎯 VELOCITY COMMAND → Pixhawk: "
                f"vx={vx:.2f}m/s (forward), vy={vy:.2f}m/s (right), vz={vz:.2f}m/s (down), yaw_rate=0.0°/s | "
                f"Position: ({current_lat:.6f}, {current_lon:.6f}, {current_alt:.1f}m) → "
                f"Target: ({target_lat:.6f}, {target_lon:.6f}, {target_alt:.1f}m) | "
                f"Error: N={error_north:.2f}m, E={error_east:.2f}m, Alt={error_alt:.2f}m, Dist={horizontal_distance:.2f}m"
            )
            
            success, msg = await self.drone_service.set_velocity_body(vx, vy, vz, 0.0)
            
            if not success:
                logger.warning(f"Velocity command failed: {msg}")
            
            await asyncio.sleep(0.1)  # 10Hz control loop
        
        return False, "Execution stopped or paused"
    
    def _clamp(self, value: float, min_val: float, max_val: float) -> float:
        """Clamp a value to a range."""
        return max(min_val, min(max_val, value))
    
    # ========== Status Queries ==========
    
    @property
    def is_executing(self) -> bool:
        """Check if a mission is currently executing."""
        return self._executing
    
    @property
    def active_mission_id(self) -> Optional[str]:
        """Get the currently active mission ID."""
        return self._active_mission_id
    
    def get_current_waypoint(self) -> Optional[Dict[str, Any]]:
        """Get the current waypoint being executed."""
        if not self._active_mission_id:
            return None
        
        with self._missions_lock:
            mission = self._missions.get(self._active_mission_id)
            if mission and mission.current_index < len(mission.waypoints):
                return mission.waypoints[mission.current_index].to_dict()
        
        return None
