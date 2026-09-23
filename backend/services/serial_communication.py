"""
Serial Communication Service for Backend
Handles USB serial communication with mobile app via telemetry radio

Replaces HTTP/WebSocket endpoints with direct serial protocol
"""

import asyncio
import json
import logging
import serial
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, Callable
from collections import deque

logger = logging.getLogger("serial_comm")

# Message queue for outgoing messages
OutgoingQueue = deque[Dict[str, Any]]


class SerialCommunicationManager:
    """
    Manages serial communication with mobile app
    """

    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 115200,
        timeout: float = 0.1,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial: Optional[serial.Serial] = None
        self.connected = False
        self.running = False

        # Message handlers
        self.message_handlers: Dict[str, Callable] = {}

        # Outgoing message queue
        self.outgoing_queue: OutgoingQueue = deque(maxlen=100)

        # Line buffer for incoming data
        self.line_buffer = ""

    def register_handler(self, message_type: str, handler: Callable) -> None:
        """
        Register a handler for a specific message type

        Args:
            message_type: Type of message to handle
            handler: Async function to handle the message
        """
        self.message_handlers[message_type] = handler
        logger.info(f"Registered handler for message type: {message_type}")

    async def connect(self) -> bool:
        """
        Connect to serial port

        Returns:
            True if connected successfully
        """
        try:
            logger.info(f"Connecting to serial port {self.port} @ {self.baudrate}")
            
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                write_timeout=1.0,
            )

            self.connected = True
            logger.info("Serial connection established")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to serial port: {e}")
            self.connected = False
            return False

    async def disconnect(self) -> None:
        """Close serial connection"""
        self.running = False

        if self.serial and self.serial.is_open:
            try:
                self.serial.close()
                logger.info("Serial connection closed")
            except Exception as e:
                logger.error(f"Error closing serial port: {e}")

        self.serial = None
        self.connected = False

    async def send_message(self, message: Dict[str, Any]) -> bool:
        """
        Send message to mobile app

        Args:
            message: Message dictionary

        Returns:
            True if sent successfully
        """
        if not self.connected or not self.serial:
            logger.warning("Serial not connected, cannot send message")
            return False

        try:
            # Add timestamp if not present
            if "timestamp" not in message:
                message["timestamp"] = datetime.utcnow().isoformat() + "Z"

            # Serialize to JSON and add newline
            data = json.dumps(message) + "\n"

            # Write to serial
            self.serial.write(data.encode("utf-8"))
            self.serial.flush()

            return True

        except Exception as e:
            logger.error(f"Failed to send serial message: {e}")
            return False

    def queue_message(self, message: Dict[str, Any]) -> None:
        """
        Add message to outgoing queue

        Args:
            message: Message to send
        """
        self.outgoing_queue.append(message)

    async def send_response(
        self,
        request_id: str,
        message_type: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Send response to a request

        Args:
            request_id: ID of the original request
            message_type: Type of response message
            data: Response data

        Returns:
            True if sent successfully
        """
        message = {
            "type": message_type,
            "id": request_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        if data:
            message["data"] = data

        return await self.send_message(message)

    async def send_telemetry(
        self,
        telemetry_type: str,
        data: Dict[str, Any],
    ) -> bool:
        """
        Send telemetry update (no request ID)

        Args:
            telemetry_type: Type of telemetry (telemetry, battery, flight_mode, armed)
            data: Telemetry data

        Returns:
            True if sent successfully
        """
        message = {
            "type": telemetry_type,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "data": data,
        }

        return await self.send_message(message)

    async def send_error(
        self,
        request_id: Optional[str],
        error: str,
        code: str = "ERROR",
    ) -> bool:
        """
        Send error message

        Args:
            request_id: ID of the request that caused error (if any)
            error: Error message
            code: Error code

        Returns:
            True if sent successfully
        """
        message = {
            "type": "error",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "data": {
                "error": error,
                "code": code,
            },
        }

        if request_id:
            message["id"] = request_id

        return await self.send_message(message)

    async def read_message(self) -> Optional[Dict[str, Any]]:
        """
        Read and parse a message from serial with robust buffering
        Handles fragmented messages from 915MHz radio transmission

        Returns:
            Parsed message dict or None
        """
        if not self.connected or not self.serial:
            return None

        try:
            # Read available data
            if self.serial.in_waiting > 0:
                data = self.serial.read(self.serial.in_waiting).decode("utf-8", errors='ignore')
                self.line_buffer += data
                logger.debug(f"[SERIAL RX] Received {len(data)} bytes, buffer now {len(self.line_buffer)} bytes")
                
                # CRITICAL: Wait 150ms for radio to transmit all chunks
                # Radio sends messages in 2-5 packets with 20-50ms delays
                await asyncio.sleep(0.15)
                
                # Read additional packets that arrived during wait (up to 5 rounds)
                additional_rounds = 0
                while self.serial.in_waiting > 0 and additional_rounds < 5:
                    data = self.serial.read(self.serial.in_waiting).decode("utf-8", errors='ignore')
                    self.line_buffer += data
                    additional_rounds += 1
                    logger.debug(f"[SERIAL RX] Additional packet #{additional_rounds}: {len(data)} bytes, buffer now {len(self.line_buffer)} bytes")
                    
                    # Wait a bit more for stragglers
                    await asyncio.sleep(0.05)

            # Process complete lines (newline-delimited JSON)
            if "\n" in self.line_buffer:
                lines = self.line_buffer.split("\n")
                self.line_buffer = lines[-1]  # Keep incomplete line in buffer

                # Process all complete lines
                for line in lines[:-1]:
                    line = line.strip()
                    if not line:
                        continue
                    
                    logger.info(f"[SERIAL RX] Processing line ({len(line)} chars): {line[:100]}...")
                    
                    # Try to parse directly first
                    try:
                        message = json.loads(line)
                        logger.info(f"[SERIAL RX] ✅ Parsed message type: {message.get('type')}")
                        return message
                    except json.JSONDecodeError as e:
                        # If direct parse fails, try recovery
                        logger.warning(f"[SERIAL RX] Parse failed, attempting recovery...")
                        
                        # Try to find valid JSON object in the line
                        best_json = None
                        for start_idx in range(len(line)):
                            if line[start_idx] == "{":
                                # Count braces from this starting point
                                brace_count = 0
                                for end_idx in range(start_idx, len(line)):
                                    if line[end_idx] == "{":
                                        brace_count += 1
                                    elif line[end_idx] == "}":
                                        brace_count -= 1
                                        if brace_count == 0:
                                            # Found matching closing brace
                                            candidate = line[start_idx:end_idx+1]
                                            # Try to parse to validate
                                            try:
                                                test = json.loads(candidate)
                                                # Check if it has required fields
                                                if 'type' in test or 'data' in test or 'action' in test:
                                                    best_json = candidate
                                                    logger.info(f"[SERIAL RX] ✅ Recovered valid message")
                                                    break
                                            except:
                                                pass
                                if best_json:
                                    break
                        
                        if best_json:
                            try:
                                message = json.loads(best_json)
                                logger.info(f"[SERIAL RX] ✅ Parsed recovered message type: {message.get('type')}")
                                return message
                            except:
                                pass
                        
                        logger.error(f"[SERIAL RX] ❌ Cannot recover valid JSON from: {line[:200]}")

            return None

        except Exception as e:
            logger.error(f"Error reading from serial: {e}")
            return None

    async def handle_message(self, message: Dict[str, Any]) -> None:
        """
        Handle incoming message with fallback for fragmented messages

        Args:
            message: Parsed message dictionary
        """
        message_type = message.get("type")
        message_id = message.get("id")

        logger.info(f"[HANDLER] Processing message type: {message_type}, id: {message_id}")

        # FALLBACK: If no "type" but has "action", treat as control_command
        # This handles fragmented messages from radio where beginning is lost
        if not message_type and "action" in message:
            logger.info(f"[HANDLER] No type field, but found 'action' - treating as control_command")
            message_type = "control_command"
            # Wrap action in data field if not already there
            if "data" not in message:
                message["data"] = {
                    "action": message.get("action"),
                    "altitude": message.get("altitude", 2.5)
                }

        if not message_type:
            logger.warning("Received message without type and no action field")
            await self.send_error(message_id, "Missing message type", "INVALID_MESSAGE")
            return

        # Get handler for this message type
        handler = self.message_handlers.get(message_type)

        if not handler:
            logger.warning(f"No handler for message type: {message_type}")
            await self.send_error(
                message_id, f"Unknown message type: {message_type}", "UNKNOWN_TYPE"
            )
            return

        try:
            # Call handler
            logger.info(f"[HANDLER] Calling handler for {message_type}")
            await handler(message)
            logger.info(f"[HANDLER] Handler completed for {message_type}")

        except Exception as e:
            logger.error(f"Error handling message {message_type}: {e}", exc_info=True)
            await self.send_error(
                message_id, f"Internal error: {str(e)}", "INTERNAL_ERROR"
            )

    async def run(self) -> None:
        """
        Main loop for reading/writing serial messages
        """
        self.running = True

        logger.info("Starting serial communication loop")

        while self.running:
            try:
                # Read incoming message
                message = await self.read_message()
                if message:
                    await self.handle_message(message)

                # Send queued messages
                while self.outgoing_queue:
                    outgoing = self.outgoing_queue.popleft()
                    await self.send_message(outgoing)

                # Small delay to prevent CPU spinning
                await asyncio.sleep(0.01)

            except Exception as e:
                logger.error(f"Error in serial communication loop: {e}", exc_info=True)
                await asyncio.sleep(1.0)

        logger.info("Serial communication loop stopped")


# Global singleton instance
_serial_comm: Optional[SerialCommunicationManager] = None


def get_serial_comm() -> SerialCommunicationManager:
    """Get the global serial communication manager"""
    global _serial_comm
    if _serial_comm is None:
        # Get port from config
        from config import SERIAL_PORT, SERIAL_BAUDRATE

        _serial_comm = SerialCommunicationManager(
            port=SERIAL_PORT,
            baudrate=SERIAL_BAUDRATE,
        )
    return _serial_comm


async def init_serial_communication() -> SerialCommunicationManager:
    """
    Initialize serial communication

    Returns:
        SerialCommunicationManager instance
    """
    serial_comm = get_serial_comm()

    # Connect to serial port
    success = await serial_comm.connect()
    if not success:
        logger.error("Failed to initialize serial communication")
        raise RuntimeError("Failed to connect to serial port")

    logger.info("Serial communication initialized successfully")
    return serial_comm