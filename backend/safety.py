"""
Safety module for drone control system.
Implements geofencing, altitude limits, battery monitoring, and emergency stop handling.
"""

import math
import time
import logging
from typing import Optional, Tuple
from dataclasses import dataclass, field
from math import radians, sin, cos, sqrt, atan2

from config import (
    SAFETY_MAX_ALTITUDE,
    SAFETY_MAX_DISTANCE,
    SAFETY_MIN_BATTERY,
    SAFETY_BATTERY_WARNING,
    TELEMETRY_STALE_THRESHOLD,
    MAX_VELOCITY_HORIZONTAL
)

logger = logging.getLogger("safety_manager")


@dataclass
class SafetyConfig:
    """Safety configuration parameters - defaults loaded from config.py"""
    max_altitude: float = field(default_factory=lambda: SAFETY_MAX_ALTITUDE)  # meters
    max_distance: float = field(default_factory=lambda: SAFETY_MAX_DISTANCE)  # meters from home
    min_battery: float = field(default_factory=lambda: SAFETY_MIN_BATTERY)  # percentage - critical
    battery_warning_threshold: float = field(default_factory=lambda: SAFETY_BATTERY_WARNING)  # percentage - warning
    home_position: Optional[Tuple[float, float, float]] = None  # (lat, lon, alt)
    telemetry_stale_threshold: float = field(default_factory=lambda: TELEMETRY_STALE_THRESHOLD)  # seconds


class SafetyManager:
    """Manages safety checks and limits for drone operations"""
    
    def __init__(self, config: SafetyConfig = None):
        self.config = config or SafetyConfig()
        self.current_position: Optional[Tuple[float, float, float]] = None
        self.current_altitude: float = 0.0  # Absolute altitude
        self.current_relative_altitude: float = 0.0  # Relative altitude (above takeoff)
        self.home_altitude: float = 0.0  # Home/takeoff altitude
        self.battery_level: float = 100.0
        self.is_armed: bool = False
        self.current_heading: float = 0.0  # Current heading/yaw in degrees (0-360, 0 = North)
        
        # Telemetry health tracking
        self._last_position_update: Optional[float] = None
        self._last_battery_update: Optional[float] = None
        self._last_heading_update: Optional[float] = None
        self._telemetry_degraded: bool = False
        
        # Conservative limits for degraded telemetry
        self._degraded_velocity_limit_factor: float = 0.5  # 50% of normal limits
        
        # When True, telemetry will not trigger auto-land; agent is deciding (safety evaluation).
        self._agent_safety_evaluation_in_progress: bool = False
    
    def set_agent_safety_evaluation(self, in_progress: bool):
        """Set when the agent is running safety evaluation. Telemetry skips auto-land while True."""
        self._agent_safety_evaluation_in_progress = in_progress
    
    def set_home_position(self, lat: float, lon: float, alt: float):
        """Set the home position for geofencing"""
        self.config.home_position = (lat, lon, alt)
        self.home_altitude = alt
    
    def update_position(self, lat: float, lon: float, alt: float, relative_alt: float = None):
        """Update current drone position
        
        Args:
            lat: Latitude
            lon: Longitude
            alt: Absolute altitude (AMSL)
            relative_alt: Relative altitude above takeoff (if available)
        """
        self.current_position = (lat, lon, alt)
        self.current_altitude = alt
        self._last_position_update = time.time()
        
        if relative_alt is not None:
            self.current_relative_altitude = relative_alt
        elif self.home_altitude > 0:
            # Calculate relative altitude if home altitude is known
            self.current_relative_altitude = alt - self.home_altitude
        
        # Check if we were in degraded mode and can now exit
        if self._telemetry_degraded:
            self._check_telemetry_health()
    
    def update_battery(self, battery_percentage: float):
        """Update battery level"""
        self.battery_level = battery_percentage
        self._last_battery_update = time.time()
        
        # Check if we were in degraded mode and can now exit
        if self._telemetry_degraded:
            self._check_telemetry_health()
    
    def update_heading(self, yaw_deg: float):
        """Update current heading/yaw in degrees.
        
        Args:
            yaw_deg: Yaw angle in degrees (-180 to 180 or 0 to 360)
        """
        # Normalize to 0-360 range
        self.current_heading = yaw_deg % 360
        if self.current_heading < 0:
            self.current_heading += 360
        self._last_heading_update = time.time()
    
    def get_heading(self) -> Tuple[float, bool]:
        """Get current heading with staleness check.
        
        Returns:
            Tuple of (heading_degrees, is_stale)
        """
        is_stale = self._last_heading_update is None or \
                   (time.time() - self._last_heading_update) > self.config.telemetry_stale_threshold
        return self.current_heading, is_stale
    
    def is_position_stale(self) -> bool:
        """Check if position data is stale."""
        if self._last_position_update is None:
            return True
        return (time.time() - self._last_position_update) > self.config.telemetry_stale_threshold
    
    def is_battery_stale(self) -> bool:
        """Check if battery data is stale."""
        if self._last_battery_update is None:
            return True
        return (time.time() - self._last_battery_update) > self.config.telemetry_stale_threshold
    
    def get_position_age(self) -> Optional[float]:
        """Get age of position data in seconds."""
        if self._last_position_update is None:
            return None
        return time.time() - self._last_position_update
    
    def get_battery_age(self) -> Optional[float]:
        """Get age of battery data in seconds."""
        if self._last_battery_update is None:
            return None
        return time.time() - self._last_battery_update
    
    def get_effective_position(self) -> Tuple[Optional[Tuple[float, float, float]], bool]:
        """
        Get effective position. Returns None if data is stale (no fallback).
        
        Returns:
            (position, is_stale): Position tuple (None if stale) and whether data is stale
        """
        if not self.is_position_stale():
            return self.current_position, False
        else:
            age = self.get_position_age()
            age_text = f"{age:.1f}s" if age is not None else "unknown"
            logger.warning(f"Position data is stale (data age: {age_text}), returning None")
            return None, True
    
    def get_effective_battery(self) -> Tuple[Optional[float], bool]:
        """
        Get effective battery level. Returns None if data is stale (no fallback).
        
        Returns:
            (battery_level, is_stale): Battery percentage (None if stale) and whether data is stale
        """
        if not self.is_battery_stale():
            return self.battery_level, False
        else:
            age = self.get_battery_age()
            age_text = f"{age:.1f}s" if age is not None else "unknown"
            logger.warning(f"Battery data is stale (data age: {age_text}), returning None")
            return None, True
    
    def _check_telemetry_health(self):
        """Check overall telemetry health and update degraded status."""
        was_degraded = self._telemetry_degraded
        
        # Consider degraded if any critical telemetry is stale
        is_now_degraded = self.is_position_stale() or self.is_battery_stale()
        
        if is_now_degraded and not was_degraded:
            logger.warning("Telemetry health degraded - using conservative limits")
            self._telemetry_degraded = True
        elif not is_now_degraded and was_degraded:
            logger.info("Telemetry health restored - normal limits active")
            self._telemetry_degraded = False
    
    def get_telemetry_health(self) -> dict:
        """Get telemetry health status."""
        return {
            "is_degraded": self._telemetry_degraded,
            "position_stale": self.is_position_stale(),
            "battery_stale": self.is_battery_stale(),
            "position_age": self.get_position_age(),
            "battery_age": self.get_battery_age(),
        }
    
    def check_battery(self) -> Tuple[bool, Optional[str]]:
        """
        Check battery level and return (is_safe, warning_message)
        Returns:
            (True, None) if safe
            (False, "warning") if warning threshold
            (False, "critical") if critical threshold
        """
        if self.battery_level < self.config.min_battery:
            return False, "critical"
        elif self.battery_level < self.config.battery_warning_threshold:
            return False, "warning"
        return True, None
    
    def check_altitude(self, target_altitude: float) -> Tuple[bool, float]:
        """
        Check if altitude is within limits
        Returns: (is_safe, capped_altitude)
        """
        if target_altitude > self.config.max_altitude:
            return False, self.config.max_altitude
        return True, target_altitude
    
    def check_distance(self, lat: float, lon: float) -> Tuple[bool, float]:
        """
        Check if position is within geofence
        Returns: (is_safe, distance_from_home)
        """
        if self.config.home_position is None or self.current_position is None:
            return True, 0.0
        
     
        
        home_lat, home_lon = self.config.home_position[0], self.config.home_position[1]
        lat1, lon1 = radians(home_lat), radians(home_lon)
        lat2, lon2 = radians(lat), radians(lon)
        
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        distance = 6371000 * c  # Earth radius in meters
        
        if distance > self.config.max_distance:
            logger.warning(
                f"Geofence check failed: current=({lat:.6f}, {lon:.6f}), "
                f"home=({home_lat:.6f}, {home_lon:.6f}), "
                f"distance={distance:.1f}m, max={self.config.max_distance}m"
            )
            return False, distance
        return True, distance
    
    def validate_velocity_command(self, vx: float, vy: float, vz: float, 
                                   current_lat: float, current_lon: float) -> Tuple[bool, str]:
        """
        Validate velocity command against safety limits.
        Uses conservative limits when telemetry is degraded.
        
        Returns: (is_valid, error_message)
        """
        # Update telemetry health status
        self._check_telemetry_health()
        
        # Check battery (use effective battery which handles stale data)
        effective_battery, is_battery_stale = self.get_effective_battery()
        
        # If battery data is stale/None, reject command
        if is_battery_stale or effective_battery is None:
            return False, f"Battery data unavailable (stale data age: {self.get_battery_age():.1f}s) - cannot execute command"
        
        battery_safe, battery_msg = self.check_battery()
        if not battery_safe and battery_msg == "critical":
            return False, "Battery critical - cannot execute command"
        
        # Check distance (estimate future position)
        # Simple check: if moving, estimate next position
        if self.config.home_position:
            # Use effective position - returns None if data is stale (no fallback)
            effective_position, is_position_stale = self.get_effective_position()
            
            if is_position_stale or effective_position is None:
                # Position data unavailable (stale)
                return False, f"Position data unavailable (stale data age: {self.get_position_age():.1f}s) - cannot execute command"
            
            # Normal geofence check with valid position data
            lat, lon, _ = effective_position
            is_safe, distance = self.check_distance(lat, lon)
            if not is_safe:
                return False, f"Geofence violation - distance: {distance:.1f}m"
        
        # Apply velocity limit reduction when telemetry is degraded
        velocity_limit_factor = 1.0
        if self._telemetry_degraded:
            velocity_limit_factor = self._degraded_velocity_limit_factor
            max_velocity = MAX_VELOCITY_HORIZONTAL * velocity_limit_factor
            
            velocity_magnitude = math.sqrt(vx**2 + vy**2 + vz**2)
            if velocity_magnitude > max_velocity:
                return False, f"Telemetry degraded - velocity limited to {max_velocity:.1f} m/s"
        
        # Check altitude limits for vertical movement
        # In body frame: vz positive = down, vz negative = up
        # We use relative altitude (above takeoff) for the limit check
        if vz < 0:  # Moving up (negative vz means ascending)
            # Use relative altitude if available and valid, otherwise fall back to absolute
            if self.current_relative_altitude > 0 and not math.isnan(self.current_relative_altitude):
                current_alt = self.current_relative_altitude
            elif not math.isnan(self.current_altitude):
                current_alt = self.current_altitude
            else:
                # If altitude data is invalid, deny command for safety
                if self._telemetry_degraded:
                    return False, "Altitude data unavailable - cannot ascend"
                return True, ""
            
            # Apply more conservative altitude limit when telemetry is degraded
            effective_max_altitude = self.config.max_altitude * velocity_limit_factor if self._telemetry_degraded else self.config.max_altitude
            
            # Conservative check: if current altitude is already near limit, prevent further ascent
            # Use a safety margin (1.0m) to prevent rapid altitude violations
            safety_margin = 1.0 if not self._telemetry_degraded else 3.0  # Larger margin when degraded
            if current_alt >= (effective_max_altitude - safety_margin):
                return False, f"Altitude limit exceeded - max: {effective_max_altitude}m"
            
            # Estimate altitude after a short time (0.2 seconds) to catch rapid changes
            # This is more conservative than 1 second and accounts for command frequency
            estimated_altitude = current_alt + abs(vz) * 0.2
            is_safe, capped = self.check_altitude(estimated_altitude)
            if not is_safe:
                return False, f"Altitude limit exceeded - max: {self.config.max_altitude}m"
        
        return True, ""
    
    def should_auto_land(self) -> bool:
        """Check if auto-land should be triggered. Returns False while agent is doing safety evaluation."""
        if self._agent_safety_evaluation_in_progress:
            return False
        battery_safe, battery_msg = self.check_battery()
        return not battery_safe and battery_msg == "critical"

