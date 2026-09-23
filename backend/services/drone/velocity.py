"""
Velocity control for drone service.
"""

import logging
import threading
from datetime import datetime
from typing import Optional, Tuple

from safety import SafetyManager
from .utils import sanitize_setpoint_value
from config import MAX_VELOCITY_HORIZONTAL, MAX_VELOCITY_VERTICAL, MAX_YAW_RATE

logger = logging.getLogger("drone_velocity_controller")


class VelocityController:
    """Manages velocity command state and validation."""
    
    def __init__(self, safety_manager: SafetyManager):
        self.safety_manager = safety_manager
        
        # Thread-safe velocity state management
        self._velocity_lock = threading.Lock()
        self.current_velocity: dict = {"vx": 0.0, "vy": 0.0, "vz": 0.0, "yaw_rate": 0.0}
        
        self.last_command_time: Optional[datetime] = None
        self.command_timeout_ms = 5000  # Stop drone if no command for 5 seconds
    
    def set_velocity(self, vx: float, vy: float, vz: float, yaw_rate: float, offboard_initialized: bool) -> Tuple[bool, str]:
        """
        Set velocity in body frame (non-blocking state update).
        
        This method updates the target velocity state. The background setpoint streamer
        will continuously send this velocity to PX4 to maintain offboard mode.
        
        Args:
            vx: Forward velocity (m/s)
            vy: Right velocity (m/s)
            vz: Down velocity (m/s)
            yaw_rate: Yaw rate (deg/s)
            offboard_initialized: Whether offboard mode is initialized
        
        Returns:
            (success: bool, message: str)
        """
        if not offboard_initialized:
            return False, "Offboard mode not initialized. Please takeoff first."
        
        # Sanitize all values first to prevent NaN/Infinity from entering state
        vx = sanitize_setpoint_value(vx, 0.0)
        vy = sanitize_setpoint_value(vy, 0.0)
        vz = sanitize_setpoint_value(vz, 0.0)
        yaw_rate = sanitize_setpoint_value(yaw_rate, 0.0)
        
        # Validate command
        if self.safety_manager.current_position:
            is_valid, error_msg = self.safety_manager.validate_velocity_command(
                vx, vy, vz,
                self.safety_manager.current_position[0],
                self.safety_manager.current_position[1]
            )
            
            if not is_valid:
                logger.warning(f"Velocity command rejected by safety: {error_msg}")
                return False, error_msg
        
        # Update current velocity and command time (thread-safe)
        with self._velocity_lock:
            self.current_velocity = {"vx": vx, "vy": vy, "vz": vz, "yaw_rate": yaw_rate}
            self.last_command_time = datetime.now()
        
        logger.debug(f"Updated target velocity: vx={vx:.3f}, vy={vy:.3f}, vz={vz:.3f}, yaw_rate={yaw_rate:.3f}")
        
        return True, "Velocity command accepted"
    
    def get_current_velocity(self) -> dict:
        """Get current velocity state (thread-safe)."""
        with self._velocity_lock:
            return self.current_velocity.copy()
    
    def reset_velocity(self):
        """Reset velocity to zero."""
        with self._velocity_lock:
            self.current_velocity = {"vx": 0.0, "vy": 0.0, "vz": 0.0, "yaw_rate": 0.0}
    
    def scale_velocity(self, normalized_value: float, max_value: float) -> float:
        """Scale normalized value (-1 to 1) to actual velocity"""
        return normalized_value * max_value
    
    def get_velocity_limits(self) -> dict:
        """Get velocity limits"""
        return {
            "horizontal": MAX_VELOCITY_HORIZONTAL,
            "vertical": MAX_VELOCITY_VERTICAL,
            "yaw_rate": MAX_YAW_RATE
        }
