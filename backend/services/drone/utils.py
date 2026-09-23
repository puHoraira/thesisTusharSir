"""
Utility functions for drone service.
"""

import math
import logging

logger = logging.getLogger("drone_utils")


def sanitize_setpoint_value(value: float, default: float = 0.0) -> float:
    """
    Sanitize a setpoint value to ensure it's finite (not NaN or Infinity).
    
    Args:
        value: The value to sanitize
        default: Default value to use if value is invalid
    
    Returns:
        Sanitized finite value
    """
    if not math.isfinite(value):
        logger.warning(f"Invalid setpoint value detected: {value}, using default: {default}")
        return default
    return value
