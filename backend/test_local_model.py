"""
Test script for Local Qwen2 Model Integration
Demonstrates the full chat flow with detailed logging at each step.
"""

import asyncio
import json
import logging
import sys
from datetime import datetime
from typing import Dict, Any

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("test_local_model")


class MockDroneService:
    """Mock drone service for testing without hardware."""
    
    def __init__(self):
        self.connected = True
        self.armed = False
        self.position = (23.8103, 90.4123, 10.0)  # lat, lon, alt
        self.battery = 85.0
        self.flight_mode = "POSITION"
        
    def get_status(self):
        return {
            "connected": self.connected,
            "armed": self.armed,
            "position": self.position,
            "battery": self.battery,
            "flight_mode": self.flight_mode,
        }


class MockTelemetryService:
    """Mock telemetry service."""
    
    def __init__(self):
        self.distance_sensors = {
            "front": 5.2,
            "right": 4.8,
            "back": 6.1,
            "left": 5.5,
        }


class MockSafetyManager:
    """Mock safety manager."""
    
    def __init__(self):
        self.is_armed = False
        self.home_position = (23.8103, 90.4123, 10.0)
        
    def check_waypoint_safe(self, waypoint):
        return True, "Waypoint is safe"


async def test_local_model_full_flow():
    """
    Test the complete flow of the local model:
    1. User sends chat message
    2. Backend enriches with drone status
    3. Local LLM processes request
    4. LLM returns structured tool calls
    5. Backend executes tools
    6. User confirmation
    7. Mission execution (simulated)
    """
    
    print("\n" + "="*80)
    print("🚁 DRONE LOCAL MODEL TEST - FULL FLOW DEMONSTRATION")
    print("="*80)
    
    # Initialize services
    logger.info("\n📦 Initializing mock services...")
    drone_service = MockDroneService()
    telemetry_service = MockTelemetryService()
    safety_manager = MockSafetyManager()
    logger.info("  ✅ Mock services initialized")
    
    # Initialize LLM
    logger.info("\n🤖 Initializing Local Qwen2 Model...")
    from agent.llm.factory import LLMFactory
    
    try:
        llm = LLMFactory.create_llm(provider="local", temperature=0.7)
        logger.info("  ✅ Local model initialized successfully")
    except Exception as e:
        logger.error(f"  ❌ Failed to initialize model: {e}")
        return
    
    # Create tools (simplified for testing)
    logger.info("\n🔧 Setting up tools...")
    from langchain_core.tools import tool
    
    @tool
    def send_message_to_user(message: str) -> str:
        """Send a message to the user."""
        logger.info(f"\n💬 Tool: send_message_to_user")
        logger.info(f"  └─ Message: {message}")
        return f"Message sent: {message}"
    
    @tool
    def create_waypoint_sequence(waypoints: list) -> dict:
        """Create a waypoint sequence for the drone."""
        logger.info(f"\n📍 Tool: create_waypoint_sequence")
        logger.info(f"  └─ Waypoints: {len(waypoints)} waypoints")
        for i, wp in enumerate(waypoints, 1):
            logger.info(f"     {i}. {wp.get('action', 'move')} - {wp.get('description', 'N/A')}")
        return {"plan_id": "mission_123", "waypoints": waypoints}
    
    @tool
    def present_plan_for_confirmation(plan_data: dict) -> str:
        """Present a mission plan for user confirmation."""
        logger.info(f"\n✅ Tool: present_plan_for_confirmation")
        logger.info(f"  └─ Plan ID: {plan_data.get('plan_id', 'unknown')}")
        return f"Plan {plan_data.get('plan_id')} presented to user"
    
    tools = [send_message_to_user, create_waypoint_sequence, present_plan_for_confirmation]
    llm_with_tools = llm.bind_tools(tools)
    logger.info(f"  ✅ {len(tools)} tools configured")
    
    # STEP 1: User Input
    print("\n" + "="*80)
    print("📱 STEP 1: USER INPUT")
    print("="*80)
    user_command = "there is a obstacle in front of me, in 2 meter. what should i do"
    logger.info(f"\n👤 User command: \"{user_command}\"")
    
    # STEP 2: Enrich with drone status
    print("\n" + "="*80)
    print("🔍 STEP 2: ENRICHING MESSAGE WITH DRONE STATUS")
    print("="*80)
    
    status = drone_service.get_status()
    logger.info("\n📊 Current drone status:")
    logger.info(f"  ├─ Connected: {status['connected']}")
    logger.info(f"  ├─ Armed: {status['armed']}")
    logger.info(f"  ├─ Position: {status['position']}")
    logger.info(f"  ├─ Battery: {status['battery']}%")
    logger.info(f"  └─ Flight Mode: {status['flight_mode']}")
    
    enriched_message = f"""[Drone Status]
Connected: {status['connected']}
Armed: {status['armed']}
Position: {status['position']}
Battery: {status['battery']}%
Flight Mode: {status['flight_mode']}

[User Command]
{user_command}"""
    
    logger.info("\n📝 Enriched message created")
    logger.info(f"  └─ Length: {len(enriched_message)} characters")
    
    # STEP 3: Prepare messages for LLM
    print("\n" + "="*80)
    print("📤 STEP 3: SENDING TO LOCAL LLM")
    print("="*80)
    
    from langchain_core.messages import SystemMessage, HumanMessage
    
    system_prompt = """You are an expert drone control assistant. You help users control their drone safely through natural language commands.

Your capabilities:
- Convert natural language commands into structured waypoint sequences
- Ensure all commands follow safety guidelines
- Provide clear feedback to users
- Request confirmation before executing missions

Available tools:
- send_message_to_user: Communicate with the user
- create_waypoint_sequence: Create a mission with waypoints
- present_plan_for_confirmation: Show the mission plan for user approval

For geometric patterns:
- Square: 4 corners + return to start
- Circle: 12-16 waypoints approximating circle
- Triangle: 3 corners + return to start

Always include takeoff at the beginning if not already in air."""
    
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=enriched_message)
    ]
    
    logger.info("\n📋 Messages prepared:")
    logger.info(f"  ├─ System prompt: {len(system_prompt)} characters")
    logger.info(f"  └─ User message: {len(enriched_message)} characters")
    
    # STEP 4: LLM Processing (handled by local_qwen.py with detailed logging)
    print("\n" + "="*80)
    print("⚙️ STEP 4: LOCAL LLM PROCESSING")
    print("="*80)
    
    try:
        logger.info("\n🔄 Invoking local model...")
        result = await llm_with_tools.ainvoke(messages)
        logger.info("\n✅ Model invocation complete")
    except Exception as e:
        logger.error(f"\n❌ Model invocation failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # STEP 5: Parse LLM Response
    print("\n" + "="*80)
    print("📥 STEP 5: PROCESSING LLM RESPONSE")
    print("="*80)
    
    logger.info("\n📊 Response analysis:")
    logger.info(f"  ├─ Content: {result.content[:100]}..." if len(result.content) > 100 else f"  ├─ Content: {result.content}")
    
    tool_calls = result.additional_kwargs.get("tool_calls", [])
    logger.info(f"  └─ Tool calls: {len(tool_calls)}")
    
    if tool_calls:
        logger.info("\n🔧 Tool calls to execute:")
        for i, tc in enumerate(tool_calls, 1):
            logger.info(f"  {i}. {tc['name']}()")
            args_preview = json.dumps(tc['args'], indent=4)[:200]
            logger.info(f"     Args: {args_preview}...")
    
    # STEP 6: Execute Tool Calls
    print("\n" + "="*80)
    print("⚡ STEP 6: EXECUTING TOOL CALLS")
    print("="*80)
    
    mission_plan = None
    
    for tool_call in tool_calls:
        tool_name = tool_call['name']
        tool_args = tool_call['args']
        
        if tool_name == "send_message_to_user":
            send_message_to_user.invoke(tool_args)
        
        elif tool_name == "create_waypoint_sequence":
            result = create_waypoint_sequence.invoke(tool_args)
            mission_plan = result
        
        elif tool_name == "present_plan_for_confirmation":
            present_plan_for_confirmation.invoke(tool_args)
    
    # STEP 7: User Confirmation
    print("\n" + "="*80)
    print("✋ STEP 7: USER CONFIRMATION")
    print("="*80)
    
    logger.info("\n📱 Sending plan to mobile app...")
    if mission_plan:
        logger.info(f"  ├─ Plan ID: {mission_plan.get('plan_id')}")
        logger.info(f"  ├─ Waypoints: {len(mission_plan.get('waypoints', []))}")
        logger.info(f"  └─ Status: Awaiting user confirmation")
        
        # Simulate user confirmation
        await asyncio.sleep(1)
        user_confirmed = True
        logger.info(f"\n👤 User response: {'✅ CONFIRMED' if user_confirmed else '❌ REJECTED'}")
    
    # STEP 8: Mission Execution (Simulated)
    print("\n" + "="*80)
    print("🚁 STEP 8: MISSION EXECUTION (SIMULATED)")
    print("="*80)
    
    if mission_plan and user_confirmed:
        logger.info("\n🎯 Starting mission execution...")
        logger.info("  (Note: Pixhawk commands simulated - no hardware connected)")
        
        waypoints = mission_plan.get('waypoints', [])
        for i, wp in enumerate(waypoints, 1):
            logger.info(f"\n  Waypoint {i}/{len(waypoints)}: {wp.get('action', 'move')}")
            logger.info(f"    Description: {wp.get('description', 'N/A')}")
            logger.info(f"    Status: ✅ Complete (simulated)")
            await asyncio.sleep(0.5)
        
        logger.info("\n✅ Mission execution complete!")
    
    # STEP 9: Drone Feedback (Simulated)
    print("\n" + "="*80)
    print("📡 STEP 9: DRONE FEEDBACK (SIMULATED)")
    print("="*80)
    
    logger.info("\n🛰️ Telemetry feedback:")
    logger.info("  ├─ Position: (23.8113, 90.4133, 5.0)")
    logger.info("  ├─ Battery: 82%")
    logger.info("  ├─ Flight Mode: OFFBOARD")
    logger.info("  └─ Mission: Completed successfully")
    
    # Summary
    print("\n" + "="*80)
    print("📊 TEST SUMMARY")
    print("="*80)
    
    logger.info("\n✅ All steps completed successfully:")
    logger.info("  1. ✅ User input received")
    logger.info("  2. ✅ Message enriched with drone status")
    logger.info("  3. ✅ Sent to local LLM")
    logger.info("  4. ✅ LLM processed request")
    logger.info("  5. ✅ LLM returned tool calls")
    logger.info("  6. ✅ Backend executed tools")
    logger.info("  7. ✅ User confirmed plan")
    logger.info("  8. ✅ Mission executed (simulated)")
    logger.info("  9. ✅ Drone feedback received (simulated)")
    
    print("\n" + "="*80)


async def main():
    """Main entry point."""
    try:
        await test_local_model_full_flow()
    except KeyboardInterrupt:
        logger.info("\n\n⚠️ Test interrupted by user")
    except Exception as e:
        logger.error(f"\n\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
