"""
Main DroneAgent class that orchestrates the LLM-powered drone control.
Coordinates between LangGraph state machine, tools, and mission executor.
"""

import logging
import random
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from fastapi import WebSocket
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import add_messages

from agent.llm.factory import LLMFactory
from agent.state.types import AgentState, AgentStateEnum, create_initial_state
from agent.state.graph import create_agent_graph
from agent.executor.mission import MissionExecutor
from agent.memory.summarization import needs_summarization, summarize_conversation
from agent.tools.base import ToolContext
from agent.tools.drone_status import create_drone_status_tool
from agent.tools.action import create_execute_action_tool
from agent.tools.waypoint import create_waypoint_tools
from agent.tools.mission import create_mission_progress_tool, create_mission_control_tools
from agent.tools.messaging import create_messaging_tools
from config import (
    SAFETY_BATTERY_WARNING,
    SAFETY_MAX_DISTANCE,

    DEMO_SAFETY_TRIGGER_AFTER_FIRST_WP,
)

if TYPE_CHECKING:
    from services.drone import DroneService
    from services.telemetry import TelemetryService
    from safety import SafetyManager

logger = logging.getLogger("drone_agent")

# Safety thresholds that trigger LLM evaluation
BATTERY_EVALUATION_THRESHOLD = SAFETY_BATTERY_WARNING  # Evaluate when below warning level
DISTANCE_EVALUATION_THRESHOLD = SAFETY_MAX_DISTANCE * 0.8  # Evaluate when approaching 80% of geofence


class DroneAgent:
    """
    Main agent class for LLM-powered drone control.
    
    Each WebSocket connection gets its own DroneAgent instance,
    providing isolated state and conversation history per user.
    
    The agent:
    - Orchestrates the LangGraph state machine
    - Manages tool bindings with proper context
    - Handles human-in-the-loop confirmation flow
    - Coordinates mission execution
    - Evaluates mission progress using LLM when safety conditions warrant
    """
    
    def __init__(
        self,
        session_id: str,
        websocket: WebSocket,
        drone_service: Optional["DroneService"] = None,
        telemetry_service: Optional["TelemetryService"] = None,
        safety_manager: Optional["SafetyManager"] = None,
    ):
        """
        Initialize a new DroneAgent.
        
        Args:
            session_id: Unique session identifier
            websocket: WebSocket connection for this session
            drone_service: DroneService instance
            telemetry_service: TelemetryService instance
            safety_manager: SafetyManager instance
        """
        self.session_id = session_id
        self.websocket = websocket
        self.drone_service = drone_service
        self.telemetry_service = telemetry_service
        self.safety_manager = safety_manager or (drone_service.safety_manager if drone_service else None)
        
        # Initialize mission executor
        self.mission_executor = MissionExecutor(
            drone_service=self.drone_service,
            safety_manager=self.safety_manager,
        )
        
        # Set up mission callbacks
        self.mission_executor.set_callbacks(
            on_waypoint_complete=self._on_waypoint_complete,
            on_mission_complete=self._on_mission_complete,
            on_mission_error=self._on_mission_error,
        )
        
        # Track progress update indices for each mission
        self._mission_update_indices: Dict[str, List[int]] = {}  # mission_id -> list of waypoint indices for updates
        
        # Create tool context
        self.tool_context = ToolContext(
            drone_service=self.drone_service,
            telemetry_service=self.telemetry_service,
            safety_manager=self.safety_manager,
            mission_executor=self.mission_executor,
            websocket=websocket,
            session_id=session_id,
        )
        
        # Create tools with bound context
        self.tools = self._create_tools()
        
        # Create LLM
        self.llm = LLMFactory.create_llm()
        
        # Create state graph (pass executor and safety manager for evaluation node)
        self.graph = create_agent_graph(
            self.llm, 
            self.tools,
            mission_executor=self.mission_executor,
            safety_manager=self.safety_manager,
            on_message=self._send_message,
        )
        
        # Initialize state
        self.state: AgentState = create_initial_state(session_id)
        
        # Serial communication support
        self._serial_comm = None
        self._serial_message_id = None
        
        logger.info(f"DroneAgent initialized for session {session_id}")
    
    def _create_tools(self) -> List[Any]:
        """Create all tools with the bound context."""
        tools = []
        
        # Drone status tool
        tools.append(create_drone_status_tool(self.tool_context))
        
        # Action tool
        tools.append(create_execute_action_tool(self.tool_context))
        
        # Waypoint tool (create only)
        tools.append(create_waypoint_tools(self.tool_context))
        
        # Mission tools (progress and evaluation)
        tools.append(create_mission_progress_tool(self.tool_context))
        tools.append(create_mission_control_tools(self.tool_context))
        
        # Messaging tools
        send_msg, present_plan = create_messaging_tools(self.tool_context)
        tools.append(send_msg)
        tools.append(present_plan)
        
        logger.info(f"Created {len(tools)} tools for agent")
        return tools
    
    async def process_command(self, user_command: str) -> Dict[str, Any]:
        """
        Process a natural language command from the user.
        
        This is the main entry point for user commands. It:
        1. Updates state with the user command
        2. Runs the LangGraph state machine
        3. Returns the response
        
        Args:
            user_command: Natural language command from user
            
        Returns:
            Dictionary with response details
        """
        
        # Get essential status to include in message (reduces tool calls)
        essential_status = self._get_essential_status()
        
        # Update state with new command
        self.state["user_command"] = user_command
        self.state["awaiting_user_input"] = False
        self.state["current_state"] = AgentStateEnum.RECEIVE_COMMAND.value
        self.state["essential_status"] = essential_status  # Store for graph to use
        
        # Reset confirmation state
        self.state["plan_confirmed"] = None
        self.state["user_feedback"] = None
        
        try:
            # Run the graph
            result_state = await self._run_graph()
            
            # Update our state
            self.state = result_state
            
            # Check if waiting for confirmation
            if result_state.get("awaiting_plan_confirmation"):
                logger.info("Graph paused for plan confirmation")
                return {
                    "status": "awaiting_confirmation",
                    "plan_data": result_state.get("plan_pending_confirmation"),
                    "session_id": self.session_id,
                }
            
            # If there's an LLM response and we're waiting for user input,
            # send it to the user via WebSocket (fallback for when LLM doesn't use tools)
            llm_response = result_state.get("llm_response")
            if llm_response and result_state.get("awaiting_user_input"):
                await self._send_message(llm_response, message_type="info")
                logger.info(f"Sent LLM response to user: {llm_response[:50]}...")
            
            # Return response
            return {
                "status": "completed",
                "response": llm_response,
                "current_state": result_state.get("current_state"),
                "session_id": self.session_id,
            }
            
        except Exception as e:
            logger.error(f"Command processing error: {e}", exc_info=True)
            self.state["error_state"] = str(e)
            self.state["current_state"] = AgentStateEnum.ERROR.value
            
            return {
                "status": "error",
                "error": str(e),
                "session_id": self.session_id,
            }
    
    async def handle_plan_confirmation(
        self,
        plan_id: str,
        confirmed: bool,
        feedback: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Handle user confirmation or abort of a plan.
        
        This is the human-in-the-loop checkpoint response.
        
        Args:
            plan_id: The plan being confirmed/aborted
            confirmed: True if user confirms, False if abort
            feedback: Optional feedback from user (especially on abort)
            
        Returns:
            Dictionary with result details
        """
        logger.info(f"Plan confirmation received: {plan_id} - {'confirmed' if confirmed else 'aborted'}")
        
        # Verify this is the pending plan
        pending_plan = self.state.get("plan_pending_confirmation") or {}
        pending_plan_id = pending_plan.get("plan_id") if pending_plan else None
        
        # If no pending plan in state, check if the mission exists in the executor
        # (this handles the case where state wasn't properly updated but mission was created)
        if not pending_plan_id:
            mission_info = self.mission_executor.get_mission_info(plan_id)
            if mission_info:
                logger.info(f"Plan {plan_id} found in executor but not in state, proceeding")
                pending_plan_id = plan_id
            else:
                return {
                    "status": "error",
                    "error": f"Plan {plan_id} is not pending confirmation (no pending plan)",
                    "session_id": self.session_id,
                }
        
        if pending_plan_id != plan_id:
            return {
                "status": "error",
                "error": f"Plan {plan_id} does not match pending plan {pending_plan_id}",
                "session_id": self.session_id,
            }
        
        # Update state with confirmation
        self.state["plan_confirmed"] = confirmed
        self.state["user_feedback"] = feedback
        
        if confirmed:
            # Start mission execution
            mission_id = plan_id
            success, message = await self.mission_executor.start_mission(mission_id)
            
            if success:
                self.state["mission_active"] = True
                self.state["current_state"] = AgentStateEnum.IDLE.value  # Graph is done, execution is external
                self.state["awaiting_plan_confirmation"] = False
                
                # Send confirmation message
                await self._send_message(
                    "Mission confirmed! Starting execution now...",
                    message_type="execution"
                )
                
                return {
                    "status": "executing",
                    "mission_id": mission_id,
                    "message": message,
                    "session_id": self.session_id,
                }
            else:
                self.state["error_state"] = message
                return {
                    "status": "error",
                    "error": message,
                    "session_id": self.session_id,
                }
        else:
            # User aborted - return to receive command
            self.state["awaiting_plan_confirmation"] = False
            self.state["plan_pending_confirmation"] = None
            self.state["current_state"] = AgentStateEnum.RECEIVE_COMMAND.value
            self.state["plan_confirmed"] = None
            self.state["user_feedback"] = None
            
            # Clear the aborted mission from the executor so it doesn't interfere
            # with future plans
            self.mission_executor.clear_mission(plan_id)
            logger.info(f"Cleared aborted mission from executor: {plan_id}")
            
            if feedback:
                # User provided feedback - re-run graph to create new plan
                # Get fresh essential status
                essential_status = self._get_essential_status()
                
                # Add feedback as a human message so LLM can respond to it
                feedback_message = f"[Plan Rejected] I rejected the previous plan. Feedback: {feedback}"
                if essential_status:
                    feedback_message = f"{essential_status}\n\n{feedback_message}"
                
                messages = list(self.state.get("messages", []))
                messages.append(HumanMessage(content=feedback_message))
                self.state["messages"] = messages
                self.state["user_command"] = feedback
                self.state["essential_status"] = essential_status
                self.state["awaiting_user_input"] = False
                
                logger.info(f"User provided feedback on abort, re-running graph: {feedback}")
                
                try:
                    # Re-run the graph to create a new plan based on feedback
                    result_state = await self._run_graph()
                    self.state = result_state
                    
                    # Check if new plan is awaiting confirmation
                    if result_state.get("awaiting_plan_confirmation"):
                        return {
                            "status": "new_plan_pending",
                            "message": "Created new plan based on your feedback.",
                            "session_id": self.session_id,
                        }
                    
                    return {
                        "status": "processed",
                        "message": "Processed your feedback.",
                        "session_id": self.session_id,
                    }
                except Exception as e:
                    logger.error(f"Error creating new plan from feedback: {e}")
                    await self._send_message(
                        f"Sorry, I encountered an error while creating a new plan: {str(e)}",
                        message_type="error"
                    )
                    return {
                        "status": "error",
                        "error": str(e),
                        "session_id": self.session_id,
                    }
            else:
                # No feedback - just wait for new command
                self.state["awaiting_user_input"] = True
                await self._send_message(
                    "The plan has been cancelled. Let me know what you'd like to do instead.",
                    message_type="info"
                )
                
                return {
                    "status": "aborted",
                    "message": "Plan aborted. Waiting for new command.",
                    "session_id": self.session_id,
                }
    
    async def _run_graph(self) -> AgentState:
        """
        Run the LangGraph state machine.
        
        Returns:
            Updated state after graph execution
        """
        # Invoke the graph with current state
        # Using async stream for step-by-step execution
        final_state = self.state
        
        async for event in self.graph.astream(self.state):
            # event is dict with node name -> output state
            for node_name, node_output in event.items():
                logger.info(f"Graph node executed: {node_name}")
                final_state = node_output
        
        return final_state
    
    async def trigger_evaluation(self, mission_id: str, reason: str) -> Dict[str, Any]:
        """
        Trigger LLM evaluation of the mission through the graph.
        
        This sets up the state for evaluation and runs the graph,
        which will route to the evaluate_mission node.
        
        Args:
            mission_id: The mission being evaluated
            reason: Reason for triggering evaluation
            
        Returns:
            Dictionary with evaluation result
        """
        logger.info(f"Triggering evaluation for mission {mission_id}: {reason}")
        
        # Defer telemetry-triggered auto-land so the agent can decide (abort/continue/update)
        if self.safety_manager:
            self.safety_manager.set_agent_safety_evaluation(True)
        
        # Set evaluation trigger in state
        self.state["evaluation_triggered"] = True
        self.state["evaluation_mission_id"] = mission_id
        self.state["evaluation_reason"] = reason
        
        try:
            # Run the graph (will route to evaluate_mission -> tools -> END; eval tool does the work)
            result_state = await self._run_graph()
            
            # Update our state (evaluation flags were cleared by clear_evaluation_state node)
            self.state = result_state
            
            # Agent already messaged user via send_message_to_user; no extra "Mission continue" line
            return {
                "status": "evaluated",
                "mission_active": result_state.get("mission_active", False),
                "response": result_state.get("llm_response", ""),
                "session_id": self.session_id,
            }
            
        except Exception as e:
            logger.error(f"Evaluation graph error: {e}", exc_info=True)
            # Clear evaluation state so next process_command does not re-enter evaluate_mission
            self.state["evaluation_triggered"] = False
            self.state["evaluation_mission_id"] = None
            self.state["evaluation_reason"] = None
            # Resume mission on error to avoid leaving drone hanging
            self.mission_executor.resume_mission()
            return {
                "status": "error",
                "error": str(e),
                "session_id": self.session_id,
            }
        finally:
            # Re-enable telemetry auto-land now that agent has finished deciding
            if self.safety_manager:
                self.safety_manager.set_agent_safety_evaluation(False)
    
    async def _send_message(self, message: str, message_type: str = "info"):
        """Send a message to the user via WebSocket or Serial."""
        # Check if using serial
        if hasattr(self, '_serial_comm') and self._serial_comm:
            await self._send_message_serial(message, message_type, self._serial_comm)
            return
        
        # Otherwise use WebSocket
        if not self.websocket:
            logger.warning(f"No WebSocket, message not sent: {message}")
            return
        
        payload = {
            "type": "ai_message",
            "role": "assistant",
            "content": message,
            "message_type": message_type,
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
        }
        
        try:
            await self.websocket.send_json(payload)
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
    
    async def _send_message_serial(self, message: str, message_type: str = "info", serial_comm=None):
        """Send a message to the user via Serial."""
        if not serial_comm:
            logger.warning(f"No serial_comm, message not sent: {message}")
            return
        
        payload = {
            "type": "ai_message",
            "role": "assistant",
            "content": message,
            "message_type": message_type,
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
        }
        
        try:
            await serial_comm.send_message(payload)
        except Exception as e:
            logger.error(f"Failed to send serial message: {e}")
    
    async def process_command_serial(self, user_command: str, message_id: str, serial_comm) -> Dict[str, Any]:
        """
        Process a command from serial interface.
        Similar to process_command but uses serial for output.
        """
        # Temporarily store serial_comm for this request
        self._serial_comm = serial_comm
        self._serial_message_id = message_id
        
        try:
            result = await self.process_command(user_command)
            return result
        finally:
            self._serial_comm = None
            self._serial_message_id = None
    
    async def confirm_plan_serial(self, plan_id: str, message_id: str, serial_comm):
        """Confirm a plan via serial interface."""
        self._serial_comm = serial_comm
        self._serial_message_id = message_id
        
        try:
            result = await self.handle_plan_confirmation(plan_id, confirmed=True, feedback=None)
            return result
        finally:
            self._serial_comm = None
            self._serial_message_id = None
    
    async def abort_plan_serial(self, plan_id: str, feedback: str, message_id: str, serial_comm):
        """Abort a plan via serial interface."""
        self._serial_comm = serial_comm
        self._serial_message_id = message_id
        
        try:
            result = await self.handle_plan_confirmation(plan_id, confirmed=False, feedback=feedback)
            return result
        finally:
            self._serial_comm = None
            self._serial_message_id = None
    
    # ========== Mission Callbacks with LLM Evaluation ==========
    
    def _calculate_progress_update_indices(self, total_waypoints: int) -> List[int]:
        """
        Calculate which waypoint indices should trigger progress updates.
        Scales with mission length: more waypoints = more updates.
        
        Examples:
        - 5 waypoints → 2 updates (indices: ~2, 5)
        - 10 waypoints → 3-4 updates (indices: ~3, 6, 9, 10)
        - 20 waypoints → 6-7 updates (indices: ~3, 6, 9, 12, 15, 18, 20)
        
        Args:
            total_waypoints: Total number of waypoints in the mission
            
        Returns:
            List of waypoint indices (1-indexed) where updates should be sent
        """
        if total_waypoints <= 1:
            return [1] if total_waypoints == 1 else []
        
        # Calculate number of updates based on mission length
        # Base: 2 updates minimum
        # Additional: ~1 update per 3-4 waypoints
        if total_waypoints <= 5:
            num_updates = 2
        elif total_waypoints <= 10:
            num_updates = 3
        elif total_waypoints <= 15:
            num_updates = 4
        elif total_waypoints <= 20:
            num_updates = 6
        else:
            # For very long missions, use formula: 2 + (total // 3)
            num_updates = min(2 + (total_waypoints // 3), total_waypoints)
        
        # Always include the last waypoint
        # Distribute the rest evenly across the mission with slight randomization
        update_indices = []
        
        if num_updates == 1:
            update_indices = [total_waypoints]
        else:
            # Calculate base intervals
            interval = total_waypoints / num_updates
            
            # Generate indices with slight randomization (±10% of interval)
            for i in range(num_updates - 1):
                base_index = int((i + 1) * interval)
                # Add slight randomization: ±10% of interval, but ensure within bounds
                random_offset = random.randint(
                    -max(1, int(interval * 0.1)),
                    max(1, int(interval * 0.1))
                )
                index = max(1, min(total_waypoints - 1, base_index + random_offset))
                update_indices.append(index)
            
            # Always include the last waypoint
            update_indices.append(total_waypoints)
        
        # Remove duplicates and sort
        update_indices = sorted(list(set(update_indices)))
        
        logger.debug(f"Progress update indices for {total_waypoints} waypoints: {update_indices}")
        return update_indices
    
    async def _on_waypoint_complete(self, waypoint_id: str, mission_id: str):
        """
        Called when a waypoint is completed.
        Checks safety conditions and triggers LLM evaluation if needed.
        
        This callback only sets evaluation triggers - LLM evaluation is
        handled by the graph's evaluate_mission node.
        """
        logger.info(f"Waypoint {waypoint_id} completed in mission {mission_id}")
        
        # Get current progress
        mission_info = self.mission_executor.get_mission_info(mission_id)
        if not mission_info:
            return
        
        current = mission_info.get("current_index", 0)
        total = len(mission_info.get("waypoints", []))
        remaining = total - current
        
        # Calculate and store update indices if not already set
        if mission_id not in self._mission_update_indices:
            self._mission_update_indices[mission_id] = self._calculate_progress_update_indices(total)
        
        # Check if current waypoint index should trigger a progress update
        update_indices = self._mission_update_indices[mission_id]
        if current in update_indices:
            await self._send_message(
                f"Progress: Completed waypoint {current}/{total}",
                message_type="execution"
            )
        
        # Demo: optionally trigger safety re-eval after first waypoint so the
        # EVALUATE_MISSION flow can be shown (normally triggered by real low battery/geofence).
        if DEMO_SAFETY_TRIGGER_AFTER_FIRST_WP and current == 1 and self.safety_manager and remaining > 0:
            self.safety_manager.update_battery(10.0)
            logger.info("Demo: injected low battery to trigger safety re-evaluation")
        
        # Check safety conditions
        needs_evaluation, reason = self._check_safety_conditions()
        
        if needs_evaluation and remaining > 0:
            logger.info(f"Safety condition triggered LLM evaluation: {reason}")
            
            # Pause mission
            self.mission_executor.pause_mission()
        
            
            # Trigger evaluation through the graph
            try:
                await self.trigger_evaluation(mission_id, reason)
            except Exception as e:
                logger.error(f"LLM evaluation failed: {e}")
                # On error, resume mission to avoid leaving drone hanging
                self.mission_executor.resume_mission()
                await self._send_message(
                    f"Evaluation error: {e}. Resuming mission.",
                    message_type="warning"
                )
    
    def _get_essential_status(self) -> str:
        """
        Get essential drone status information to include in user messages.
        This reduces the need for the LLM to call get_drone_status tool.
        
        Returns:
            Formatted string with essential status info
        """
        if not self.drone_service or not self.safety_manager:
            return "[Status: Drone service unavailable]"
        
        status_parts = []
        
        # Connection and arming
        status_parts.append(f"Connected: {self.drone_service.connected}")
        status_parts.append(f"Armed: {self.safety_manager.is_armed}")
        
        # Position
        position, is_position_stale = self.safety_manager.get_effective_position()
        if position and not is_position_stale:
            lat, lon, alt = position
            rel_alt = self.safety_manager.current_relative_altitude or (alt - self.safety_manager.home_altitude if self.safety_manager.home_altitude > 0 else alt)
            status_parts.append(f"Position: ({lat:.6f}, {lon:.6f}), Altitude: {rel_alt:.1f}m")
            
            # Distance from home
            if self.safety_manager.config.home_position:
                _, distance = self.safety_manager.check_distance(lat, lon)
                status_parts.append(f"Distance from home: {distance:.1f}m")
        else:
            status_parts.append("Position: Unavailable (stale data)")
        
        # Battery
        battery, is_battery_stale = self.safety_manager.get_effective_battery()
        if battery is not None and not is_battery_stale:
            battery_safe, battery_msg = self.safety_manager.check_battery()
            status_parts.append(f"Battery: {battery:.1f}% ({battery_msg if battery_msg else 'OK'})")
        else:
            status_parts.append("Battery: Unavailable (stale data)")
        
        # Flight mode
        if self.telemetry_service and hasattr(self.telemetry_service, 'streams'):
            streams = self.telemetry_service.streams
            if hasattr(streams, '_latest_flight_mode') and streams._latest_flight_mode:
                mode = streams._latest_flight_mode
                mode_name = mode.name if hasattr(mode, 'name') else str(mode)
                status_parts.append(f"Flight mode: {mode_name}")
        
        # Offboard status
        if self.drone_service.offboard_initialized:
            status_parts.append(f"Offboard: {'Active' if self.drone_service.offboard_active else 'Initialized'}")
        
        return "[Status: " + ", ".join(status_parts) + "]"
    
    def _check_safety_conditions(self) -> tuple[bool, str]:
        """
        Check if current safety conditions warrant LLM evaluation.
        
        Returns:
            Tuple of (needs_evaluation, reason)
        """
        if not self.safety_manager:
            return False, ""
        
        # Check battery using effective battery (handles stale data)
        battery, is_battery_stale = self.safety_manager.get_effective_battery()
        if battery is not None and not is_battery_stale and battery < BATTERY_EVALUATION_THRESHOLD:
            return True, f"Battery at {battery:.1f}% (below {BATTERY_EVALUATION_THRESHOLD}%)"
        
        # Check distance from home
        position, is_position_stale = self.safety_manager.get_effective_position()
        if position is not None and not is_position_stale:
            lat, lon, _ = position
            is_safe, distance = self.safety_manager.check_distance(lat, lon)
            if distance > DISTANCE_EVALUATION_THRESHOLD:
                return True, f"Distance from home: {distance:.1f}m (approaching {SAFETY_MAX_DISTANCE}m limit)"
        
        # Check for critical battery level
        battery_safe, battery_msg = self.safety_manager.check_battery()
        if not battery_safe:
            battery_val, _ = self.safety_manager.get_effective_battery()
            return True, f"Battery {battery_msg}: {battery_val:.1f}%" if battery_val is not None else f"Battery {battery_msg}"
        
        return False, ""
    
    async def _on_mission_complete(self, mission_id: str, status: str = "completed"):
        """Called when a mission ends (completed or aborted)."""
        logger.info(f"Mission {mission_id} ended: {status}")
        
        self.state["mission_active"] = False
        self.state["current_state"] = AgentStateEnum.COMPLETE.value
        self.state["awaiting_user_input"] = True
        
        if status == "aborted":
            # LLM already sent the abort message during evaluation flow; skip duplicate
            pass
        else:
            completion_message = "Mission completed successfully! All waypoints reached."
            messages = self.state.get("messages", [])
            messages = list(messages)
            messages.append(AIMessage(content=completion_message))
            self.state["messages"] = messages
            await self._send_message(completion_message, message_type="success")
        
        # Clean up mission tracking
        self._mission_update_indices.pop(mission_id, None)
        
        # Clear mission from storage to prevent memory accumulation
        self.mission_executor.clear_mission(mission_id)
    
    async def _on_mission_error(self, mission_id: str, error: str):
        """Called when a mission encounters an error."""
        logger.error(f"Mission {mission_id} error: {error}")
        
        self.state["mission_active"] = False
        self.state["current_state"] = AgentStateEnum.ERROR.value
        self.state["error_state"] = error
        self.state["awaiting_user_input"] = True
        
        error_message = f"Mission error: {error}. The drone has been stopped."
        
        # Add error message to state so LLM knows mission failed
        messages = self.state.get("messages", [])
        messages = list(messages)
        messages.append(AIMessage(content=error_message))
        self.state["messages"] = messages
        
        await self._send_message(
            error_message,
            message_type="error"
        )
        
        # Clean up mission tracking
        self._mission_update_indices.pop(mission_id, None)
        
        # Clear failed mission from storage to prevent memory accumulation
        self.mission_executor.clear_mission(mission_id)
    
    # ========== State Management ==========
    
    def get_state(self) -> Dict[str, Any]:
        """Get current agent state."""
        return dict(self.state)
    
    def is_awaiting_confirmation(self) -> bool:
        """Check if waiting for plan confirmation."""
        return self.state.get("awaiting_plan_confirmation", False)
    
    def is_mission_active(self) -> bool:
        """Check if a mission is currently executing."""
        return self.state.get("mission_active", False)
    
    async def emergency_stop(self) -> Dict[str, Any]:
        """Emergency stop - abort mission and stop drone."""
        logger.warning("Emergency stop triggered")
        
        # Stop mission if active
        if self.mission_executor.is_executing:
            await self.mission_executor.stop_mission("Emergency stop")
        
        # Send emergency stop to drone
        if self.drone_service:
            success, msg = await self.drone_service.emergency_stop()
        else:
            success, msg = False, "Drone service not available"
        
        # Update state
        self.state["mission_active"] = False
        self.state["awaiting_plan_confirmation"] = False
        self.state["current_state"] = AgentStateEnum.IDLE.value
        
        await self._send_message(
            "Emergency stop executed. All operations halted.",
            message_type="warning"
        )
        
        return {
            "status": "stopped",
            "success": success,
            "message": msg,
            "session_id": self.session_id,
        }
    
    async def clear_conversation_history(self) -> Dict[str, Any]:
        """
        Clear conversation history and reset state to initial state.
        This allows starting a fresh conversation while keeping the session.
        
        Returns:
            Dict with status and message
        """
        logger.info(f"Clearing conversation history for session {self.session_id}")
        
        # Stop any active mission
        if self.mission_executor.is_executing:
            await self.mission_executor.stop_mission("Conversation cleared")
        
        # Reset state to initial state (preserving session_id)
        initial_state = create_initial_state(self.session_id)
        self.state = initial_state
        
        return {
            "status": "cleared",
            "message": "Conversation history cleared",
            "session_id": self.session_id,
        }
    
    async def cleanup(self):
        """Clean up resources when session ends."""
        logger.info(f"Cleaning up agent session {self.session_id}")
        
        # Stop any active mission
        if self.mission_executor.is_executing:
            await self.mission_executor.stop_mission("Session ended")
        
        # Clear all missions from storage
        self.mission_executor.clear_all_missions()


# Factory function for creating agents
def create_agent(
    websocket: WebSocket,
    drone_service: Optional["DroneService"] = None,
    telemetry_service: Optional["TelemetryService"] = None,
) -> DroneAgent:
    """
    Create a new DroneAgent instance for a WebSocket connection.
    
    Args:
        websocket: The WebSocket connection
        drone_service: DroneService instance
        telemetry_service: TelemetryService instance
        
    Returns:
        New DroneAgent instance
    """
    session_id = f"session_{uuid.uuid4().hex[:8]}"
    
    return DroneAgent(
        session_id=session_id,
        websocket=websocket,
        drone_service=drone_service,
        telemetry_service=telemetry_service,
    )
