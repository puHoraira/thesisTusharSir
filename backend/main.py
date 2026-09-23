"""
FastAPI backend for real-time drone control system.
Main application entry point with minimal code.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.logging import DefaultFormatter
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from services import get_drone_service, get_telemetry_service
from routers import control, telemetry, agent
import threading
from dotenv import load_dotenv
from config import SITL_HOST, SITL_PORT, HARDWARE_MODE, USE_SERIAL_COMM

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)

def _configure_uvicorn_style_logging() -> None:
    formatter = DefaultFormatter(fmt="%(levelprefix)s [%(name)s] %(message)s", use_colors=True)
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.setFormatter(formatter)

_configure_uvicorn_style_logging()
logger = logging.getLogger("main")

# Global flag for shutdown
shutdown_event = asyncio.Event()
force_exit = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan event handler for startup and shutdown.
    Handles drone connection initialization and cleanup.
    """
    # Startup
    drone_service = get_drone_service()
    # Pass shutdown_event to drone_service for early shutdown detection
    drone_service.set_shutdown_event(shutdown_event)
    # Pass shutdown_event to telemetry_service for graceful shutdown
    telemetry_service = get_telemetry_service()
    telemetry_service.shutdown_event = shutdown_event
    background_tasks = []
    connect_task = None
    serial_task = None
    
    # Don't override signal handlers - let uvicorn handle them
    # Just set up our shutdown_complete event for the lifespan handler
    shutdown_complete = threading.Event()
    
    # Initialize Serial Communication if enabled
    serial_comm = None
    if USE_SERIAL_COMM:
        try:
            logger.info("Initializing serial communication...")
            from services.serial_communication import init_serial_communication
            from services.serial_handlers import register_all_handlers
            
            serial_comm = await init_serial_communication()
            
            # Register all message handlers
            register_all_handlers(serial_comm)
            logger.info("All serial message handlers registered")
            
            # Start serial communication loop
            serial_task = asyncio.create_task(serial_comm.run())
            background_tasks.append(serial_task)
            logger.info("Serial communication started")
            
        except Exception as e:
            logger.error(f"Failed to initialize serial communication: {e}")
            # Continue without serial (fallback to HTTP/WebSocket)
    
    # Start connection in background (non-blocking) - but make it cancellable
    connect_task = None
    try:
        logger.info("Attempting to connect to drone...")
        # Start connection as a task but don't wait for it
        if HARDWARE_MODE:
            # Hardware mode: Use connection from config (can be serial or TCP)
            from config import TELEMETRY_DEVICE, TELEMETRY_BAUDRATE
            
            # Support serial, TCP, and UDP connections
            if TELEMETRY_DEVICE.startswith("serial://"):
                connection_string = TELEMETRY_DEVICE
                logger.info(f"Hardware mode: Connecting via DIRECT SERIAL at {TELEMETRY_DEVICE}")
            elif TELEMETRY_DEVICE.startswith("tcp://"):
                connection_string = TELEMETRY_DEVICE
                logger.info(f"Hardware mode: Connecting via TCP at {TELEMETRY_DEVICE}")
            elif TELEMETRY_DEVICE.startswith("udpout://") or TELEMETRY_DEVICE.startswith("udpin://"):
                connection_string = TELEMETRY_DEVICE
                logger.info(f"Hardware mode: Connecting via UDP to MAVProxy at {TELEMETRY_DEVICE}")
            else:
                # Fallback to UDP MAVProxy (legacy)
                connection_string = f"udpout://127.0.0.1:{SITL_PORT}"
                logger.info(f"Hardware mode: Fallback to UDP MAVProxy at 127.0.0.1:{SITL_PORT}")
        else:
            # SITL mode: Listen on port 14540 where PX4 sends heartbeats
            connection_string = f"udpin://0.0.0.0:{SITL_PORT}"
            logger.info(f"SITL mode: Listening for PX4 on UDP port {SITL_PORT}")
        connect_task = asyncio.create_task(
            drone_service.connect(system_address=connection_string)
        )
        
        # Wait for connection with longer timeout (rate setting adds ~0.5s)
        # Use asyncio.shield to prevent cancellation on timeout
        try:
            connected = await asyncio.wait_for(asyncio.shield(connect_task), timeout=10.0)
        except asyncio.TimeoutError:
            # Connection is taking longer, continue without blocking
            logger.info("Drone connection in progress...")
            connected = False
            # Task continues running in background due to asyncio.shield
        
        async def start_background_tasks():
            """Start background tasks after connection is established."""
            # Detect and recover from in-flight reconnection scenarios
            try:
                recovery_attempted, recovery_msg = await drone_service.detect_and_recover_flight_state()
                if recovery_attempted:
                    logger.info(f"Reconnection recovery: {recovery_msg}")
                else:
                    logger.debug(f"No reconnection recovery needed: {recovery_msg}")
            except Exception as recovery_error:
                logger.warning(f"Reconnection detection failed (non-fatal): {recovery_error}")
            
            # Start telemetry streaming
            telemetry_task = asyncio.create_task(telemetry_service.start_streaming())
            # Start command timeout handler
            timeout_task = asyncio.create_task(drone_service.command_timeout_handler())
            
            background_tasks.extend([telemetry_task, timeout_task])
            logger.info("Background tasks started")
        
        if connected:
            logger.info("Drone connected successfully")
            await start_background_tasks()
            
            # Start telemetry serial broadcaster if serial enabled
            if USE_SERIAL_COMM and serial_comm:
                async def telemetry_serial_broadcaster():
                    """Broadcast telemetry via serial"""
                    try:
                        while not shutdown_event.is_set():
                            # Build telemetry data from streams
                            telemetry_data = {}
                            
                            # Add position data if available
                            if telemetry_service.streams._latest_position:
                                pos = telemetry_service.streams._latest_position
                                telemetry_data["position"] = {
                                    "latitude": pos.latitude_deg,
                                    "longitude": pos.longitude_deg,
                                    "altitude_relative": pos.relative_altitude_m,
                                    "altitude_absolute": pos.absolute_altitude_m,
                                }
                            
                            # Add velocity if available
                            if telemetry_service.streams._latest_velocity:
                                vel = telemetry_service.streams._latest_velocity
                                import math
                                vn = vel.velocity.north_m_s
                                ve = vel.velocity.east_m_s
                                vd = vel.velocity.down_m_s
                                telemetry_data["velocity"] = {
                                    "resultant": math.sqrt(vn**2 + ve**2 + vd**2)
                                }
                            
                            # Add heading if available
                            if telemetry_service.streams._latest_heading is not None:
                                telemetry_data["heading"] = telemetry_service.streams._latest_heading
                            
                            # Add battery if available
                            if telemetry_service.streams._latest_battery:
                                bat = telemetry_service.streams._latest_battery
                                telemetry_data["battery"] = {
                                    "percentage": bat.remaining_percent,
                                    "voltage": bat.voltage_v,
                                }
                            
                            # Add flight mode if available
                            if telemetry_service.streams._latest_flight_mode:
                                mode = telemetry_service.streams._latest_flight_mode
                                telemetry_data["flight_mode"] = mode.name if hasattr(mode, 'name') else str(mode)
                            
                            # Add armed status if available
                            if telemetry_service.streams._latest_armed is not None:
                                telemetry_data["armed"] = telemetry_service.streams._latest_armed
                            
                            # Send if we have any data
                            if telemetry_data:
                                await serial_comm.send_telemetry("telemetry", telemetry_data)
                                logger.debug(f"Broadcasted telemetry via serial: {list(telemetry_data.keys())}")
                            else:
                                logger.debug("No telemetry data available yet")
                            
                            await asyncio.sleep(0.1)  # 10 Hz
                    except asyncio.CancelledError:
                        logger.debug("Telemetry serial broadcaster cancelled")
                    except Exception as e:
                        logger.error(f"Telemetry serial broadcaster error: {e}")
                
                telemetry_broadcaster_task = asyncio.create_task(telemetry_serial_broadcaster())
                background_tasks.append(telemetry_broadcaster_task)
                logger.info("Starting telemetry serial broadcaster")
        else:
            # Connection in progress - monitor and start tasks when connected
            logger.info("Drone connection in progress...")
            
            async def wait_for_connection():
                """Wait for connection task to complete and start background tasks."""
                try:
                    result = await connect_task
                    if result:
                        logger.info("Drone connected successfully")
                        await start_background_tasks()
                    else:
                        logger.warning("Drone connection failed - will retry on next request")
                except asyncio.CancelledError:
                    logger.debug("Connection wait cancelled")
                except Exception as e:
                    logger.error(f"Connection error: {e}")
            
            # Start monitoring task
            asyncio.create_task(wait_for_connection())
            
    except Exception as e:
        logger.error(f"Startup error: {e}")
        if connect_task:
            connect_task.cancel()
    
    yield  # Application runs here
    
    # Shutdown - Phased graceful shutdown
    # This runs when uvicorn shuts down (after signal or on exit)

    logger.info("Lifespan shutdown started")

    
    try:
        # Phase 1: Set shutdown event and request shutdown from all services
        shutdown_event.set()
        telemetry_service.request_shutdown()
        drone_service.shutdown_requested.set()
        
        # Stop serial communication if enabled
        if USE_SERIAL_COMM and serial_comm:
            try:
                await serial_comm.disconnect()
                logger.debug("Serial communication stopped")
            except Exception as e:
                logger.warning(f"Serial disconnect error (non-fatal): {e}")
        
        # Phase 2: Cancel connection task if still running
        if connect_task and not connect_task.done():
            connect_task.cancel()
            try:
                await asyncio.wait_for(connect_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as e:
                logger.debug(f"Connection cancellation error (non-fatal): {e}")
        # Phase 3: Stop telemetry streams (give them 1s to gracefully exit)
        for task in background_tasks:
            if not task.done():
                task.cancel()
        
        if background_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*background_tasks, return_exceptions=True),
                    timeout=1.0  # Reduced from 2.0 to 1.0
                )
                logger.debug("Telemetry streams stopped")
            except asyncio.TimeoutError:
                logger.warning("Some telemetry streams did not stop in time")
            except Exception as e:
                logger.warning(f"Telemetry shutdown error (non-fatal): {e}")
        
        # Phase 4: Stop drone offboard tasks and disconnect
        try:
            await asyncio.wait_for(drone_service.disconnect(), timeout=1.0)  # Reduced from 2.0 to 1.0
            logger.debug("Drone disconnected successfully")
        except asyncio.TimeoutError:
            logger.warning("Drone disconnect timeout")
        except Exception as e:
            logger.warning(f"Drone disconnect error (non-fatal): {e}")
        

        logger.info("Lifespan shutdown complete")

        
        # Signal watchdog that shutdown completed successfully
        shutdown_complete.set()
    except Exception as e:
        logger.error(f"Error during shutdown: {e}", exc_info=True)
        shutdown_complete.set()  # Set even on error to prevent watchdog timeout


# Initialize FastAPI app with lifespan
app = FastAPI(title="Drone Control API", lifespan=lifespan)

# CORS middleware for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Get service instances
drone_service = get_drone_service()
telemetry_service = get_telemetry_service()

# Include routers
app.include_router(control.router)
app.include_router(telemetry.router)
app.include_router(agent.router)

# Mount static assets
# Ensure absolute path or relative from where main.py is run (backend/)
app.mount("/assets", StaticFiles(directory="../assets"), name="assets")

@app.get("/landing")
async def landing():
    """Serve the landing page"""
    return FileResponse("../assets/landing.html")


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "online",
        "drone_connected": drone_service.connected,
        "control_connections": len(control.control_connections),
        "telemetry_connections": len(telemetry_service.connections)
    }


@app.get("/status")
async def status():
    """Detailed status endpoint with offboard state"""
    return {
        "drone": {
            "connected": drone_service.connected,
            "offboard_initialized": drone_service.offboard_initialized,
            "offboard_active": drone_service.offboard_active,
            "failsafe_active": drone_service.failsafe_active,
            "failsafe_recovery_attempts": drone_service.failsafe_recovery_attempts,
        },
        "connections": {
            "control": len(control.control_connections),
            "telemetry": len(telemetry_service.connections),
            "agent_sessions": len(agent.get_active_sessions()) if hasattr(agent, 'get_active_sessions') else 0
        },
        "config": {
            "hardware_mode": os.getenv("HARDWARE_MODE", "false"),
            "offboard_rate_hz": os.getenv("OFFBOARD_SETPOINT_RATE_HZ", "10.0"),
            "takeoff_altitude": os.getenv("DEFAULT_TAKEOFF_ALTITUDE", "2.5")
        }
    }


if __name__ == "__main__":
    import uvicorn
    # Use port 8000 for mobile app compatibility
    uvicorn.run(app, host="0.0.0.0", port=8000)
