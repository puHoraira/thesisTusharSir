"""
Configuration constants for drone control system.
"""
import os 
from dotenv import load_dotenv

load_dotenv()

# Connection configuration
SITL_HOST = os.getenv("SITL_HOST", "127.0.0.1")  # PX4 SITL host address
SITL_PORT = int(os.getenv("SITL_PORT", "14540"))  # PX4 MAVLink port

# Hardware mode - telemetry radio configuration
TELEMETRY_DEVICE = os.getenv("TELEMETRY_DEVICE", "/dev/ttyUSB0")  # Serial device for telemetry radio
TELEMETRY_BAUDRATE = int(os.getenv("TELEMETRY_BAUDRATE", "57600"))  # Baud rate for telemetry radio

# Serial communication configuration (for direct mobile communication)
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")  # USB serial port for mobile communication
SERIAL_BAUDRATE = int(os.getenv("SERIAL_BAUDRATE", "115200"))  # Baud rate for mobile serial
USE_SERIAL_COMM = os.getenv("USE_SERIAL_COMM", "false").lower() in ("true", "yes", "on")  # Enable serial communication instead of HTTP/WebSocket

# =============================================================================
# Velocity Limits
# =============================================================================

MAX_VELOCITY_HORIZONTAL = float(os.getenv("MAX_VELOCITY_HORIZONTAL"))  # m/s - maximum horizontal velocity
MAX_VELOCITY_VERTICAL = float(os.getenv("MAX_VELOCITY_VERTICAL"))    # m/s - maximum vertical velocity
MAX_YAW_RATE = float(os.getenv("MAX_YAW_RATE"))            # deg/s - maximum yaw rotation rate

# =============================================================================
# Offboard Mode Configuration
# =============================================================================

# PX4 requires setpoints at >2Hz to maintain offboard mode
OFFBOARD_SETPOINT_RATE_HZ = float(os.getenv("OFFBOARD_SETPOINT_RATE_HZ"))           # Hz - setpoint streaming rate
OFFBOARD_MIN_RATE_HZ = float(os.getenv("OFFBOARD_MIN_RATE_HZ"))                 # Hz - PX4 minimum requirement
OFFBOARD_INIT_SETPOINT_DURATION = float(os.getenv("OFFBOARD_INIT_SETPOINT_DURATION"))      # seconds - duration to send setpoints before starting offboard
OFFBOARD_INIT_RETRY_ATTEMPTS = int(os.getenv("OFFBOARD_INIT_RETRY_ATTEMPTS"))           # number of retry attempts for offboard initialization
OFFBOARD_MODE_CHECK_INTERVAL = float(os.getenv("OFFBOARD_MODE_CHECK_INTERVAL"))         # seconds - check offboard mode health interval

# Calculate interval from rate
OFFBOARD_SETPOINT_INTERVAL = 1.0 / OFFBOARD_SETPOINT_RATE_HZ

# =============================================================================
# Takeoff Configuration
# =============================================================================

DEFAULT_TAKEOFF_ALTITUDE = float(os.getenv("DEFAULT_TAKEOFF_ALTITUDE"))    # meters - default takeoff altitude
MIN_TAKEOFF_ALTITUDE = float(os.getenv("MIN_TAKEOFF_ALTITUDE"))        # meters - minimum allowed takeoff altitude
TAKEOFF_OFFBOARD_DELAY = float(os.getenv("TAKEOFF_OFFBOARD_DELAY"))      # seconds - delay before offboard init after takeoff

# =============================================================================
# Hardware Mode Configuration
# =============================================================================

HARDWARE_MODE = os.getenv("HARDWARE_MODE").lower() in ("true", "yes", "on")  # Set to True when using real hardware (not SITL)
HARDWARE_TIMEOUT_MULTIPLIER = float(os.getenv("HARDWARE_TIMEOUT_MULTIPLIER")) if HARDWARE_MODE else 1.0  # 50% longer timeouts for hardware

# =============================================================================
# Connection Health Monitoring
# =============================================================================

CONNECTION_MONITOR_INTERVAL = float(os.getenv("CONNECTION_MONITOR_INTERVAL"))          # seconds - connection check interval
CONNECTION_LOSS_TIMEOUT = float(os.getenv("CONNECTION_LOSS_TIMEOUT"))              # seconds - before connection considered lost
CONNECTION_RECONNECT_MAX_ATTEMPTS = int(os.getenv("CONNECTION_RECONNECT_MAX_ATTEMPTS"))      # max auto-reconnection attempts
CONNECTION_RECONNECT_BACKOFF_BASE = float(os.getenv("CONNECTION_RECONNECT_BACKOFF_BASE"))    # seconds - base backoff between reconnect attempts

# =============================================================================
# Setpoint Streamer Configuration
# =============================================================================

SETPOINT_STREAMER_MAX_RESTARTS = int(os.getenv("SETPOINT_STREAMER_MAX_RESTARTS"))         # max auto-restart attempts
SETPOINT_STREAMER_RESTART_BACKOFF = float(os.getenv("SETPOINT_STREAMER_RESTART_BACKOFF"))    # seconds - base backoff between restarts
SETPOINT_STREAMER_RESTART_BACKOFF_MAX = float(os.getenv("SETPOINT_STREAMER_RESTART_BACKOFF_MAX"))  # seconds - maximum backoff time

# =============================================================================
# GPS Loss Handling
# =============================================================================

GPS_LOSS_ACTION = str(os.getenv("GPS_LOSS_ACTION"))    # Options: "LAND", "RTL" (Return to Launch), "HOLD"
GPS_LOSS_TIMEOUT = float(os.getenv("GPS_LOSS_TIMEOUT"))      # seconds - without GPS before failsafe action
GPS_MIN_SATELLITES = int(os.getenv("GPS_MIN_SATELLITES"))      # minimum satellites for valid GPS fix
GPS_MIN_FIX_TYPE = int(os.getenv("GPS_MIN_FIX_TYPE"))        # Options: 0=None, 2=2D Fix, 3=3D Fix (recommended)

# =============================================================================
# Telemetry Stream Configuration
# =============================================================================

TELEMETRY_STREAM_MAX_RESTARTS = int(os.getenv("TELEMETRY_STREAM_MAX_RESTARTS"))       # max auto-restart attempts per stream
TELEMETRY_STREAM_RESTART_BACKOFF = float(os.getenv("TELEMETRY_STREAM_RESTART_BACKOFF"))  # seconds - backoff between restarts
TELEMETRY_STALE_THRESHOLD = float(os.getenv("TELEMETRY_STALE_THRESHOLD"))         # seconds - before data considered stale

# =============================================================================
# State Synchronization
# =============================================================================

STATE_SYNC_INTERVAL = float(os.getenv("STATE_SYNC_INTERVAL"))           # seconds - between backend/PX4 state syncs
STATE_SYNC_MISMATCH_THRESHOLD = int(os.getenv("STATE_SYNC_MISMATCH_THRESHOLD"))   # consecutive mismatches before corrective action

# =============================================================================
# Emergency Handling
# =============================================================================

EMERGENCY_ZERO_VELOCITY_ATTEMPTS = int(os.getenv("EMERGENCY_ZERO_VELOCITY_ATTEMPTS"))   # retry attempts for zero velocity command
EMERGENCY_LAND_TIMEOUT = float(os.getenv("EMERGENCY_LAND_TIMEOUT"))           # seconds - timeout for emergency land command

# =============================================================================
# Safety Limits
# =============================================================================

SAFETY_MAX_ALTITUDE = float(os.getenv("SAFETY_MAX_ALTITUDE"))        # meters - maximum altitude above home
SAFETY_MAX_DISTANCE = float(os.getenv("SAFETY_MAX_DISTANCE"))        # meters - maximum distance from home (geofence)
SAFETY_MIN_BATTERY = float(os.getenv("SAFETY_MIN_BATTERY"))          # percentage - critical battery level (triggers auto-land)
SAFETY_BATTERY_WARNING = float(os.getenv("SAFETY_BATTERY_WARNING"))      # percentage - warning battery level

# =============================================================================
# LLM Integration Configuration
# =============================================================================

# LLM Provider Selection
LLM_PROVIDER = os.getenv("LLM_PROVIDER")  # "gemini" or "openai"

# Google Gemini Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL")

# OpenAI Configuration (for future use)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL")

# LLM Parameters
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS"))
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT"))

# =============================================================================
# Mission Executor Configuration
# =============================================================================

WAYPOINT_POSITION_TOLERANCE = float(os.getenv("WAYPOINT_POSITION_TOLERANCE"))  # meters
WAYPOINT_ALTITUDE_TOLERANCE = float(os.getenv("WAYPOINT_ALTITUDE_TOLERANCE"))  # meters
WAYPOINT_VELOCITY_TOLERANCE = float(os.getenv("WAYPOINT_VELOCITY_TOLERANCE"))  # m/s
WAYPOINT_NAVIGATION_KP = float(os.getenv("WAYPOINT_NAVIGATION_KP"))  # proportional gain
MAXIMUM_WAYPOINT_TIMEOUT = float(os.getenv("MAXIMUM_WAYPOINT_TIMEOUT"))  # seconds
METERS_PER_DEGREE_LAT = float(os.getenv("METERS_PER_DEGREE_LAT"))  # meters per degree of latitude

# =============================================================================
# Demo / Presentation
# =============================================================================
# When True, after the first waypoint completes the safety manager is fed a low
# battery value so the safety-triggered re-eval flow runs (EVALUATE_MISSION path).
# Use only for demos; leave False for normal operation.
DEMO_SAFETY_TRIGGER_AFTER_FIRST_WP = os.getenv("DEMO_SAFETY_TRIGGER_AFTER_FIRST_WP", "false").lower() in ("true", "1", "yes")

# =============================================================================
# Conversation Memory Configuration
# =============================================================================

CONVERSATION_MESSAGE_THRESHOLD = int(os.getenv("CONVERSATION_MESSAGE_THRESHOLD"))  # Trigger summarization at this count
CONVERSATION_MESSAGES_TO_SUMMARIZE = int(os.getenv("CONVERSATION_MESSAGES_TO_SUMMARIZE"))  # Messages to summarize
CONVERSATION_MESSAGES_TO_KEEP = int(os.getenv("CONVERSATION_MESSAGES_TO_KEEP"))  # Recent messages to keep in full
CONVERSATION_SUMMARY_MAX_TOKENS = int(os.getenv("CONVERSATION_SUMMARY_MAX_TOKENS"))  # Max tokens for summary