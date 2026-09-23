"""
LangGraph state machine definition for the drone agent.
Implements the state graph with human-in-the-loop confirmation.

The graph handles user commands and mission planning. Mission execution
is handled externally by MissionExecutor with pause/resume support.
LLM evaluation during execution is routed through the evaluate_mission node.
"""

import logging, json
from typing import Any, List, Literal, Optional, TYPE_CHECKING

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, RemoveMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from agent.state.types import AgentState, AgentStateEnum
from agent.llm.factory import LLMProviderUnavailableError
from agent.llm.prompts import DRONE_AGENT_SYSTEM_PROMPT, create_mission_evaluation_message, EVALUATION_FINAL_MESSAGE_HINT
from agent.memory.summarization import summarize_conversation, needs_summarization

if TYPE_CHECKING:
    from agent.executor.mission import MissionExecutor
    from safety import SafetyManager

logger = logging.getLogger("agent_graph")


def create_agent_graph(
    llm,
    tools: List[Any],
    mission_executor: Optional["MissionExecutor"] = None,
    safety_manager: Optional["SafetyManager"] = None,
    on_state_change: Optional[callable] = None,
    on_llm_message: Optional[callable] = None,
    on_message: Optional[callable] = None,
):
    """
    Create the LangGraph state machine for the drone agent.
    
    The state graph implements:
    - RECEIVE_COMMAND: Wait for and process user commands
    - PLAN_MISSION: LLM generates mission plan using tools
    - CONFIRM_PLAN: Human-in-the-loop checkpoint for plan confirmation
    - EVALUATE_MISSION: LLM evaluates mission during execution (safety-triggered)
    - ERROR: Handle errors and recover
    
    Mission execution is handled externally by MissionExecutor,
    which supports pause/resume and triggers evaluation through the graph.
    
    Args:
        llm: LangChain LLM instance (with tools bound)
        tools: List of LangChain tools
        mission_executor: MissionExecutor instance for mission state access
        safety_manager: SafetyManager instance for drone status
        on_state_change: Optional callback for state changes
        on_llm_message: Optional async callback for sending LLM messages to user
                        when LLM responds without using tools
        
    Returns:
        Compiled LangGraph
    """
    
    # Bind tools to LLM
    llm_with_tools = llm.bind_tools(tools)
    
    # Define the state graph
    graph = StateGraph(AgentState)
    
    # ========== Node Functions ==========
    
    async def summarize_messages(state: AgentState) -> AgentState:
        """
        Summarize conversation history to manage context window.
        Uses the add_messages reducer to handle RemoveMessage updates.
        """
        messages = state.get("messages", [])
        
        # Notify user (if callback provided)
        if on_message:
            await on_message("Summarizing conversation history to maintain context...", message_type="system")
        
        try:
            # Call summarization service which returns RemoveMessage updates
            # Note: llm is available from create_agent_graph closure
            message_updates, summary = await summarize_conversation(messages, llm)
            
            if summary and message_updates:
                logger.info(f"Applying {len(message_updates)} message updates (summarization)")
                
                # Notify completion
                if on_message:
                    await on_message("Conversation history summarized. Continuing...", message_type="system")

                return {
                    **state,
                    "messages": message_updates
                }
            else:
                pass
        except Exception as e:
            logger.error(f"Summarization node failed: {e}", exc_info=True)
            
        return state

    def receive_command(state: AgentState) -> AgentState:
        """
        Process incoming user command.
        Sets up the state for planning.
        """
        logger.info(f"Receiving command: {state.get('user_command', '')[:50]}...")
        
        user_command = state.get("user_command", "")
        
        # Build messages for LLM
        messages = state.get("messages", [])
        
        messages = list(messages)
        
        # Add system prompt if not present
        if not messages or not any(isinstance(m, SystemMessage) for m in messages):
            messages.insert(0, SystemMessage(content=DRONE_AGENT_SYSTEM_PROMPT))
        
        # Include essential status in user message to reduce tool calls
        essential_status = state.get("essential_status", "")
        if essential_status:
            user_message_content = f"{essential_status}\n\n{user_command}"
        else:
            user_message_content = user_command
        
        # Add user message with status
        messages.append(HumanMessage(content=user_message_content))
        
        return {
            **state,
            "current_state": AgentStateEnum.PLAN_MISSION.value,
            "messages": messages,
            "awaiting_user_input": False,
            "error_state": None,
            # Clear any stale plan confirmation state from previous commands
            "awaiting_plan_confirmation": False,
            "plan_pending_confirmation": None,
            "plan_confirmed": None,
            "user_feedback": None,
        }
    
    def plan_mission(state: AgentState) -> AgentState:
        """
        LLM generates mission plan using ReAct pattern.
        Invokes LLM with tools to create waypoints.
        """
        logger.info("Planning mission...")
        
        messages = state.get("messages", [])
        
        # Invoke LLM with tools
        try:
            response = llm_with_tools.invoke(messages)
            
            if hasattr(response, "tool_calls") and response.tool_calls:

                tool_names = []
                for tc in response.tool_calls:
                    if isinstance(tc, dict):
                        tool_names.append(tc.get("name", "tool"))
                        # Log tool arguments for detailed tracing
                        tool_name = tc.get("name", "unknown")
                        tool_args = tc.get("args", {})
                        logger.info(
                            f"📞 [LLM→TOOL] {tool_name}\n"
                            f"   Arguments: {json.dumps(tool_args, indent=2)}"
                        )
                    elif hasattr(tc, "name"):
                        tool_names.append(tc.name)
                        # Log tool arguments
                        logger.info(
                            f"📞 [LLM→TOOL] {tc.name}\n"
                            f"   Arguments: {json.dumps(tc.get('args', {}), indent=2) if hasattr(tc, 'get') else str(tc)}"
                        )
                    else:
                        tool_names.append("tool")
                
                logger.info(f"LLM made {len(response.tool_calls)} tool calls: {', '.join(tool_names)}")

                if not response.content:
                    # Create a new AIMessage with content for tool calls
                    response = AIMessage(
                        content=f"Calling tools: {', '.join(tool_names)}",
                        tool_calls=response.tool_calls,
                        additional_kwargs=response.additional_kwargs if hasattr(response, "additional_kwargs") else {},
                    )
            
            # Add AI response to messages
            messages = list(messages)
            messages.append(response)
            
            # Extract response text
            llm_response = response.content if hasattr(response, "content") else str(response)
            
            # DEBUG: Log what LLM responded with
            logger.info(f"🤖 LLM Response Content: {llm_response}")
            if hasattr(response, "tool_calls"):
                logger.info(f"🔧 Tool Calls: {len(response.tool_calls) if response.tool_calls else 0}")
            
            
            return {
                **state,
                "messages": messages,
                "llm_response": llm_response,
            }

        except LLMProviderUnavailableError as e:
            logger.warning(f"LLM provider unavailable during planning: {e}")
            return {
                **state,
                "current_state": AgentStateEnum.ERROR.value,
                "error_state": str(e),
                "llm_response": (
                    "I couldn't reach the configured language model provider. "
                    "Please switch LLM_PROVIDER to a working provider or restore API access."
                ),
            }
            
        except Exception as e:
            logger.error(f"LLM planning error: {e}")
            return {
                **state,
                "current_state": AgentStateEnum.ERROR.value,
                "error_state": str(e),
            }
    
    def check_plan_confirmation(state: AgentState) -> AgentState:
        """
        Check if plan confirmation is pending.
        Transition to CONFIRM_PLAN state if needed.
        
        Only checks messages AFTER the most recent HumanMessage to avoid
        finding stale awaiting_confirmation statuses from previous (aborted) plans.
        """
        # Check ToolMessages for awaiting_confirmation status
        messages = state.get("messages", [])
        
        # Find the index of the most recent HumanMessage (user command)
        last_human_idx = -1
        for i, msg in enumerate(messages):
            if isinstance(msg, HumanMessage):
                last_human_idx = i
        
        # Only check messages after the last HumanMessage
        messages_to_check = messages[last_human_idx + 1:] if last_human_idx >= 0 else messages
        
        for msg in reversed(messages_to_check):
            if isinstance(msg, ToolMessage):
                # Parse tool result content
                content = msg.content
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except (json.JSONDecodeError, TypeError):
                        continue
                
                if isinstance(content, dict) and content.get("status") == "awaiting_confirmation":
                    plan_id = content.get("plan_id")
                    waypoint_count = content.get("waypoint_count")
                    
                    # Sanity check: only proceed if we have a valid plan_id and waypoint_count
                    if not plan_id or not waypoint_count:
                        logger.warning(f"Found awaiting_confirmation but missing plan data: plan_id={plan_id}, waypoint_count={waypoint_count}")
                        continue
                    
                    logger.info(f"Found pending plan confirmation: {plan_id} with {waypoint_count} waypoints")
                    plan_data = {
                        "plan_id": plan_id,
                        "waypoint_count": waypoint_count,
                    }
                    return {
                        **state,
                        "current_state": AgentStateEnum.CONFIRM_PLAN.value,
                        "awaiting_plan_confirmation": True,
                        "plan_pending_confirmation": plan_data,
                    }
        
        return state
    
    def confirm_plan(state: AgentState) -> AgentState:
        """
        Human-in-the-loop checkpoint.
        Waits for user confirmation before proceeding.
        
        After confirmation, the graph ends and mission execution
        is handled externally by MissionExecutor.
        """
        plan_confirmed = state.get("plan_confirmed")
        plan_pending = state.get("plan_pending_confirmation")
        user_feedback = state.get("user_feedback")
        
        if plan_confirmed is True:
            # User confirmed - mission will be started by agent.handle_plan_confirmation
            logger.info("Plan confirmed by user")
            plan_data = state.get("plan_pending_confirmation", {})
            
            return {
                **state,
                "current_state": AgentStateEnum.IDLE.value,
                "awaiting_plan_confirmation": False,
                "mission_active": True,
                "mission_state": {
                    "mission_id": plan_data.get("plan_id"),
                    "status": "executing",
                    "total_waypoints": plan_data.get("waypoint_count", 0),
                },
            }
        
        elif plan_confirmed is False:
            # User aborted - return to receive command
            logger.info(f"Plan aborted by user. Feedback: {user_feedback}")
            
            # Add feedback to messages so LLM can respond
            messages = list(state.get("messages", []))
            feedback_text = user_feedback or "The plan was cancelled."
            messages.append(HumanMessage(content=f"[Plan Aborted] {feedback_text}"))
            
            return {
                **state,
                "current_state": AgentStateEnum.RECEIVE_COMMAND.value,
                "awaiting_plan_confirmation": False,
                "plan_pending_confirmation": None,
                "plan_confirmed": None,
                "user_feedback": None,
                "messages": messages,
                "awaiting_user_input": True,
            }
        
        # Still waiting for confirmation - only if there's actually a plan pending
        if plan_pending:
            logger.info(f"Waiting for plan confirmation (human-in-the-loop): {plan_pending.get('plan_id')}")
            return {
                **state,
                "awaiting_plan_confirmation": True,
            }
        
        # No plan pending - return to idle/receive command
        logger.info("No plan pending confirmation, returning to receive command")
        return {
            **state,
            "awaiting_plan_confirmation": False,
            "current_state": AgentStateEnum.RECEIVE_COMMAND.value,
            "awaiting_user_input": True,
        }
    
    def handle_error(state: AgentState) -> AgentState:
        """
        Handle errors and recover.
        """
        error = state.get("error_state", "Unknown error")
        logger.error(f"Agent error: {error}")
        
        return {
            **state,
            "current_state": AgentStateEnum.IDLE.value,
            "mission_active": False,
            "awaiting_user_input": True,
            "awaiting_plan_confirmation": False,
        }
    
    def evaluate_mission(state: AgentState) -> AgentState:
        """
        Add evaluation context to messages and invoke LLM with tools.
        
        This node is triggered when safety conditions warrant LLM evaluation
        during mission execution. The mission is paused before this runs.
        
        The LLM will have full message context and can use the
        `evaluate_and_update_mission` tool to decide what to do.
        """
        if not state.get("evaluation_triggered"):
            # Not triggered for evaluation, skip
            return {
                **state,
                "evaluation_triggered": False,
            }
        
        mission_id = state.get("evaluation_mission_id")
        reason = state.get("evaluation_reason", "Safety evaluation requested")
        
        logger.info(f"Evaluating mission {mission_id}: {reason}")
        
        # Get mission info from executor
        mission_info = None
        current_index = 0
        total_waypoints = 0
        remaining_count = 0
        
        if mission_executor:
            mission_info = mission_executor.get_mission_info(mission_id)
            if mission_info:
                current_index = mission_info.get("current_index", 0)
                all_waypoints = mission_info.get("waypoints", [])
                total_waypoints = len(all_waypoints)
                remaining_count = total_waypoints - current_index
        
        # Get drone status from safety manager
        status_parts = []
        
        if safety_manager:
            battery, _ = safety_manager.get_effective_battery()
            if battery is not None:
                status_parts.append(f"Battery: {battery:.1f}%")
            
            position, _ = safety_manager.get_effective_position()
            if position:
                lat, lon, _ = position
                status_parts.append(f"Position: ({lat:.6f}, {lon:.6f})")
                
                if safety_manager.config.home_position:
                    _, distance = safety_manager.check_distance(lat, lon)
                    status_parts.append(f"Distance from home: {distance:.1f}m")
            
            if safety_manager.current_relative_altitude is not None:
                status_parts.append(f"Altitude: {safety_manager.current_relative_altitude:.1f}m")
        
        status_str = ", ".join(status_parts) if status_parts else "Status unavailable"
        
        # Build evaluation message using prompt function
        eval_message = create_mission_evaluation_message(
            mission_id=mission_id,
            reason=reason,
            status_str=status_str,
            current_index=current_index,
            total_waypoints=total_waypoints,
            remaining_count=remaining_count,
        )

        # Add to messages with full context
        messages = list(state.get("messages", []))
        
        # Ensure system prompt is present
        if not messages or not any(isinstance(m, SystemMessage) for m in messages):
            messages.insert(0, SystemMessage(content=DRONE_AGENT_SYSTEM_PROMPT))
        
        messages.append(HumanMessage(content=eval_message))
        
        logger.info("Invoking LLM for mission evaluation with tool context...")
        
        try:
            # Invoke LLM with tools - LLM will call evaluate_and_update_mission
            response = llm_with_tools.invoke(messages)
            
            # Add response to messages
            messages.append(response)
            
            # Check if LLM made tool calls
            if hasattr(response, "tool_calls") and response.tool_calls:
                tool_names = [tc.get("name", "tool") if isinstance(tc, dict) else getattr(tc, "name", "tool") 
                             for tc in response.tool_calls]
                logger.info(f"LLM evaluation made tool calls: {', '.join(tool_names)}")
                
                # Return state for tool execution
                return {
                    **state,
                    "messages": messages,
                    "current_state": AgentStateEnum.EVALUATE_MISSION.value,
                }
            else:
                # LLM responded without tool call - this shouldn't happen
                logger.warning("LLM evaluation did not call evaluate_and_update_mission tool")
                
                # Resume mission to avoid leaving drone hanging
                if mission_executor:
                    mission_executor.resume_mission()
                
                return {
                    **state,
                    "messages": messages,
                    "current_state": AgentStateEnum.IDLE.value,
                    "mission_active": True,
                    "evaluation_triggered": False,
                    "evaluation_mission_id": None,
                    "evaluation_reason": None,
                    "llm_response": response.content if hasattr(response, "content") else str(response),
                }
                
        except Exception as e:
            logger.error(f"LLM evaluation error: {e}")
            # On error, resume mission to avoid leaving drone hanging
            if mission_executor:
                mission_executor.resume_mission()
            
            return {
                **state,
                "current_state": AgentStateEnum.IDLE.value,
                "mission_active": True,
                "evaluation_triggered": False,
                "evaluation_mission_id": None,
                "evaluation_reason": None,
                "error_state": f"Evaluation error: {e}",
                "llm_response": f"Evaluation error, resuming mission: {e}",
            }

        except LLMProviderUnavailableError as e:
            logger.warning(f"LLM provider unavailable during evaluation: {e}")
            if mission_executor:
                mission_executor.resume_mission()

            return {
                **state,
                "current_state": AgentStateEnum.IDLE.value,
                "mission_active": True,
                "evaluation_triggered": False,
                "evaluation_mission_id": None,
                "evaluation_reason": None,
                "error_state": str(e),
                "llm_response": (
                    "Mission evaluation is temporarily unavailable because the configured model provider "
                    "cannot be reached. The mission has been resumed."
                ),
            }
    
    def process_evaluation(state: AgentState) -> AgentState:
        """
        Second and subsequent LLM invokes during evaluation: no new eval context,
        just continue on current messages so the LLM can call evaluate_and_update_mission.
        Called after tools when the last result had no "action" (e.g. only send_message_to_user).
        """
        messages = list(state.get("messages", []))
        if not messages:
            return state
        
        logger.info("Invoking LLM for evaluation follow-up (no new context)...")
        try:
            response = llm_with_tools.invoke(messages)
            messages.append(response)
            
            if hasattr(response, "tool_calls") and response.tool_calls:
                tool_names = [tc.get("name", "tool") if isinstance(tc, dict) else getattr(tc, "name", "tool")
                             for tc in response.tool_calls]
                logger.info(f"LLM evaluation follow-up made tool calls: {', '.join(tool_names)}")
            
            return {
                **state,
                "messages": messages,
                "current_state": AgentStateEnum.EVALUATE_MISSION.value,
            }
        except LLMProviderUnavailableError as e:
            logger.warning(f"LLM provider unavailable during evaluation follow-up: {e}")
            if mission_executor:
                mission_executor.resume_mission()
            return {
                **state,
                "current_state": AgentStateEnum.IDLE.value,
                "evaluation_triggered": False,
                "evaluation_mission_id": None,
                "evaluation_reason": None,
                "error_state": str(e),
                "llm_response": (
                    "Mission evaluation follow-up is temporarily unavailable because the configured model "
                    "provider cannot be reached. The mission has been resumed."
                ),
            }
        except Exception as e:
            logger.error(f"Process evaluation LLM error: {e}")
            if mission_executor:
                mission_executor.resume_mission()
            return {
                **state,
                "current_state": AgentStateEnum.IDLE.value,
                "evaluation_triggered": False,
                "evaluation_mission_id": None,
                "evaluation_reason": None,
                "error_state": str(e),
            }
    
    def clear_evaluation_state(state: AgentState) -> AgentState:
        """Reset evaluation flags so next graph entry routes to receive_command, not evaluate_mission."""

        messages = list(state.get("messages", []))
        messages.append(HumanMessage(content=EVALUATION_FINAL_MESSAGE_HINT))
        return {
            **state,
            "messages": messages,
            "current_state": AgentStateEnum.IDLE.value,
            "evaluation_triggered": False,
            "evaluation_mission_id": None,
            "evaluation_reason": None,
            # Clear confirmation state so plan_mission routes to end/tools, not confirm_plan
            "awaiting_plan_confirmation": False,
            "plan_pending_confirmation": None,
        }
    
    
    # ========== Routing Functions ==========
    
    def _has_awaiting_confirmation_in_messages(messages: list) -> bool:
        """Check if any recent ToolMessage has awaiting_confirmation status."""
        # Look at recent messages (after last HumanMessage) for awaiting_confirmation
        last_human_idx = -1
        for i, msg in enumerate(messages):
            if isinstance(msg, HumanMessage):
                last_human_idx = i
        
        messages_to_check = messages[last_human_idx + 1:] if last_human_idx >= 0 else messages
        
        for msg in messages_to_check:
            if isinstance(msg, ToolMessage):
                content = msg.content
                if isinstance(content, str):
                    try:
                        parsed = json.loads(content)
                        status = parsed.get("status") if isinstance(parsed, dict) else None
                        if status == "awaiting_confirmation":
                            return True
                    except (json.JSONDecodeError, TypeError):
                        continue
        
        return False
    
    def should_continue_planning(state: AgentState) -> Literal["tools", "check_confirmation", "end", "error"]:
        """Determine next step after planning."""
        if state.get("error_state"):
            return "error"
        
        # Check last message for tool calls (ToolNode reads from here)
        messages = state.get("messages", [])
        if messages:
            last_message = messages[-1]
            if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                return "tools"
        
        # If there's already a plan pending confirmation, go to confirmation.
        if state.get("awaiting_plan_confirmation") or state.get("plan_pending_confirmation"):
            return "check_confirmation"
        
        # Also check if any recent ToolMessage has awaiting_confirmation
        if _has_awaiting_confirmation_in_messages(messages):
            return "check_confirmation"
        
        # No tool calls and no pending confirmation - LLM is done responding
        return "end"
    
    def _any_recent_tool_result_has_action(messages: list) -> bool:
        """True if any ToolMessage since the last AIMessage has 'action' (evaluate_and_update_mission was called this round)."""
        for msg in reversed(messages):
            if isinstance(msg, ToolMessage):
                try:
                    content = msg.content
                    parsed = json.loads(content) if isinstance(content, str) else content
                    if isinstance(parsed, dict) and "action" in parsed:
                        return True
                except (json.JSONDecodeError, TypeError):
                    pass
            elif isinstance(msg, AIMessage):
                break
        return False
    
    def should_continue_after_tools(state: AgentState) -> Literal["plan", "confirm", "eval_result", "evaluate", "error"]:
        """Determine next step after tool execution."""
        if state.get("error_state"):
            return "error"
        
        # In evaluation mode: end when eval tool was called this round (any tool has "action"); else loop
        if state.get("current_state") == AgentStateEnum.EVALUATE_MISSION.value:
            messages = state.get("messages", [])
            has_action = _any_recent_tool_result_has_action(messages)
            if has_action:
                return "eval_result"  # evaluate_and_update_mission was called → clear state and END
            return "evaluate"  # e.g. only send_message_to_user → process_evaluation for another turn
        
        if state.get("awaiting_plan_confirmation"):
            return "confirm"
        
        # Check if any tool returned awaiting_confirmation status
        # This catches the present_plan_for_confirmation tool result
        messages = state.get("messages", [])
        
        if _has_awaiting_confirmation_in_messages(messages):
            return "confirm"
        
        # Continue planning
        return "plan"
    
    def should_continue_confirmation(state: AgentState) -> Literal["end", "receive", "wait"]:
        """Determine next step after confirmation check."""
        plan_confirmed = state.get("plan_confirmed")
        
        if plan_confirmed is True:
            # Confirmed - end graph, mission starts externally
            return "end"
        elif plan_confirmed is False:
            return "receive"
        else:
            # Still waiting
            return "wait"
    
    def should_route_entry(state: AgentState) -> Literal["receive", "evaluate", "summarize"]:
        """
        Route at graph entry based on evaluation trigger or summarization need.
        """
        if state.get("evaluation_triggered"):
            return "evaluate"
            
        # Check if summarization is needed
        messages = state.get("messages", [])
        needs_sum = needs_summarization(messages)
        if needs_sum:
            return "summarize"
        
        return "receive"
    
    # ========== Build Graph ==========
    
    # Add nodes
    graph.add_node("entry_router", lambda state: state)  # Pass-through for routing
    graph.add_node("summarize_messages", summarize_messages)
    graph.add_node("receive_command", receive_command)
    graph.add_node("plan_mission", plan_mission)
    graph.add_node("tools", ToolNode(tools))
    graph.add_node("check_confirmation", check_plan_confirmation)
    graph.add_node("confirm_plan", confirm_plan)
    graph.add_node("evaluate_mission", evaluate_mission)
    graph.add_node("process_evaluation", process_evaluation)
    graph.add_node("clear_evaluation_state", clear_evaluation_state)
    graph.add_node("error", handle_error)
    
    # Set entry point with conditional routing for evaluation vs normal commands
    graph.set_entry_point("entry_router")
    
    # Route from entry based on evaluation_triggered or summarization
    graph.add_conditional_edges(
        "entry_router",
        should_route_entry,
        {
            "receive": "receive_command",
            "evaluate": "evaluate_mission",
            "summarize": "summarize_messages",
        }
    )
    
    # Add edges
    graph.add_edge("summarize_messages", "receive_command")
    graph.add_edge("receive_command", "plan_mission")
    
    graph.add_conditional_edges(
        "plan_mission",
        should_continue_planning,
        {
            "tools": "tools",
            "check_confirmation": "check_confirmation",
            "end": END,  # LLM finished without tool calls or pending confirmation
            "error": "error",
        }
    )
    
    graph.add_conditional_edges(
        "tools",
        should_continue_after_tools,
        {
            "plan": "plan_mission",
            "confirm": "check_confirmation",  # Go to check_confirmation to extract plan data first
            "eval_result": "clear_evaluation_state",  # Eval tool was called → clear flags then END
            "evaluate": "process_evaluation",  # No eval tool yet → LLM invoke again (no big message), then tools
            "error": "error",
        }
    )
    
    graph.add_edge("process_evaluation", "tools")  # After process_evaluation always run tools
    graph.add_edge("clear_evaluation_state", "plan_mission")  # So next entry routes to plan_mission
    
    graph.add_edge("check_confirmation", "confirm_plan")
    
    graph.add_conditional_edges(
        "confirm_plan",
        should_continue_confirmation,
        {
            "end": END,  # Confirmed - mission execution handled externally
            "receive": "receive_command",
            "wait": END,  # Interrupt and wait for external confirmation
        }
    )
    
    # Evaluation flow: evaluate_mission (big message once) -> tools -> [process_evaluation -> tools]* -> END when eval tool called
    graph.add_edge("evaluate_mission", "tools")
    
    graph.add_edge("error", END)
    
    # Compile and return
    return graph.compile()
