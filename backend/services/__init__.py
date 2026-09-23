"""
Services module initialization.
Provides singleton service instances for dependency injection.
"""

from services.drone import DroneService
from services.telemetry import TelemetryService
from safety import SafetyConfig

# Singleton service instances
_drone_service: DroneService = None
_telemetry_service: TelemetryService = None


def get_drone_service() -> DroneService:
    """Get or create drone service instance"""
    global _drone_service
    if _drone_service is None:
        _drone_service = DroneService(SafetyConfig())
    return _drone_service


def get_telemetry_service() -> TelemetryService:
    """Get or create telemetry service instance"""
    global _telemetry_service
    if _telemetry_service is None:
        _telemetry_service = TelemetryService(get_drone_service())
    return _telemetry_service


def initialize_services(drone_service: DroneService = None, telemetry_service: TelemetryService = None):
    """Initialize services with provided instances (for testing)"""
    global _drone_service, _telemetry_service
    if drone_service:
        _drone_service = drone_service
    if telemetry_service:
        _telemetry_service = telemetry_service

