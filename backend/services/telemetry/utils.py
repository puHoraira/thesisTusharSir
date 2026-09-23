"""
Utility functions for telemetry service.
"""

import math
from typing import Any


def sanitize_for_json(obj: Any) -> Any:
    """
    Recursively sanitize data structure to replace NaN, Infinity with null
    This ensures JSON serialization works correctly
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {key: sanitize_for_json(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [sanitize_for_json(item) for item in obj]
    else:
        return obj
