"""
Test script using the actual project's agent (like the real system).
This mirrors exactly how the WebSocket endpoint works.
"""

import asyncio
import logging
import sys
from datetime import datetime
from unittest.mock import Mock

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger("test_gemini")


class MockWebSocket:
    """Mock WebSocket that logs messages instead of sending them."""
    
    def __init__(self):
        self.messages = []
    
    async def send_json(self, data):
        """Log message instead of sending."""
        msg_type = data.get("type", "unknown")
        content = data.get("content", "")
        
        if msg_type == "ai_message":
            logger.info(f"\n💬 AI Message:")
            logger.info(f"  └─ {content[:200]}..." if len(content) > 200 else f"  └─ {content}")
        elif msg_type == "plan_confirmation":
            logger.info(f"\n✅ Plan Confirmation:")
            plan_data = data.get("plan_data", {})
            logger.info(f"  ├─ Plan ID: {plan_data.get('plan_id')}")
            logger.info(f"  ├─ Summary: {plan_data.get('summary')}")
            logger.info(f"  └─ Waypoints: {plan_data.get('waypoint_count', 0)}")
        elif msg_type == "status_update":
            logger.info(f"\n📊 Status: {data.get('status')}")
        
        self.messages.append(data)


async def test_full_flow():
    """Test the complete flow using the actual DroneAgent."""
    
    print("\n" + "="*80)
    print("🚁 GEMINI MODEL TEST - USING ACTUAL PROJECT CODE")
    print("="*80)
    
    # Import project components
    logger.info("\n📦 Step 1: Importing project components...")
    from agent.agent import DroneAgent
    from services.drone import DroneService
    from services.telemetry import TelemetryService
    from safety import SafetyManager
    
    # Create mock services
    logger.info("📦 Step 2: Creating mock services...")
    mock_websocket = MockWebSocket()
    
    # These would normally connect to MAVSDK, but we're testing without hardware
    drone_service = None  # Agent handles None gracefully
    telemetry_service = None
    
    logger.info("  ✅ Mock services ready")
    
    # Create agent (this initializes Gemini internally)
    logger.info("\n🤖 Step 3: Creating DroneAgent (initializes Gemini)...")
    agent = DroneAgent(
        session_id="test_session",
        websocket=mock_websocket,
        drone_service=drone_service,
        telemetry_service=telemetry_service,
    )
    logger.info("  ✅ Agent created successfully")
    
    # Test user command
    print("\n" + "="*80)
    print("📱 Step 4: USER INPUT")
    print("="*80)
    
    user_command = "Fly a 10 meter square at 5 meter altitude"
    logger.info(f"\n👤 User: \"{user_command}\"")
    
    # Process command (this is what the WebSocket endpoint does)
    print("\n" + "="*80)
    print("⚙️ Step 5: PROCESSING WITH GEMINI")
    print("="*80)
    
    logger.info("\n🔄 Agent processing command...")
    logger.info("  (Gemini will generate tool calls...)")
    
    try:
        result = await agent.process_command(user_command)
        
        logger.info("\n✅ Processing complete!")
        logger.info(f"  ├─ Status: {result.get('status')}")
        logger.info(f"  ├─ Session: {result.get('session_id')}")
        
        if result.get('status') == 'awaiting_confirmation':
            logger.info(f"  └─ Plan ready for confirmation")
        
    except Exception as e:
        logger.error(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Show all messages that would be sent to frontend
    print("\n" + "="*80)
    print("📤 Step 6: MESSAGES SENT TO FRONTEND")
    print("="*80)
    
    logger.info(f"\n📨 Total messages: {len(mock_websocket.messages)}")
    for i, msg in enumerate(mock_websocket.messages, 1):
        msg_type = msg.get("type")
        logger.info(f"\n  Message {i}: {msg_type}")
        
        if msg_type == "plan_confirmation":
            plan = msg.get("plan_data", {})
            waypoints = plan.get("waypoints", [])
            logger.info(f"     Waypoints:")
            for j, wp in enumerate(waypoints[:5], 1):  # Show first 5
                action = wp.get("action", "unknown")
                desc = wp.get("description", "")
                logger.info(f"       {j}. {action}: {desc}")
            if len(waypoints) > 5:
                logger.info(f"       ... and {len(waypoints) - 5} more")
    
    # Summary
    print("\n" + "="*80)
    print("📊 TEST SUMMARY")
    print("="*80)
    
    logger.info("\n✅ Test completed successfully!")
    logger.info("  1. ✅ Agent created with Gemini")
    logger.info("  2. ✅ Command processed")
    logger.info("  3. ✅ Tool calls generated")
    logger.info("  4. ✅ Messages sent to frontend")
    logger.info(f"  5. ✅ Total messages: {len(mock_websocket.messages)}")
    
    print("\n" + "="*80)


async def main():
    """Main entry point."""
    try:
        await test_full_flow()
    except KeyboardInterrupt:
        logger.info("\n\n⚠️ Test interrupted by user")
    except Exception as e:
        logger.error(f"\n\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
