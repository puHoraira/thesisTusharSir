<table>
  <tr>
    <td width="200">
      <img src="assets/logo.png" alt="SkyJARVIS Logo" width="200"/>
    </td>
    <td>
      <h1>SkyJARVIS</h1>
      <p><strong>Next-Gen AI-Powered Drone Control System</strong></p>
      <p>
        Create, command, and control PX4-based drones with natural language or precision joysticks.
        <br />
        Powered by <strong>FastAPI</strong>, <strong>React Native</strong>, and <strong>LangGraph</strong>.
      </p>
    </td>
  </tr>
</table>

<br />

SkyJARVIS is a advanced drone control platform that bridges the gap between manual piloting and autonomous AI agents. Whether you want to fly manually with low-latency controls or simply tell your drone to "scan the perimeter in a square pattern," SkyJARVIS makes it happen.

## ✨ Features

- **🧠 AI-Powered Control**: Command your drone using natural language (e.g., "Fly a 50m zig-zag pattern"). Powered by Google Gemini & OpenAI.
- **🕹️ Precision Manual Flight**: Dual virtual joysticks for intuitive control over altitude, yaw, and movement.
- **🛰️ Real-Time Telemetry**: Live streaming of GPS, altitude, battery status, and flight modes.
- **🗺️ Interactive Map**: Meaningful path visualization with active trailing and map-based context commands.
- **🛡️ Safety First**: Integrated geofencing, battery monitoring, and emergency stop protocols.
- **🔄 Smart Replanning**: Mid-mission command modifications and dynamic path adjustments.

## 🛠️ Tech Stack

- **Backend**: Python 3.8+, MAVSDK, FastAPI, LangGraph, LangChain.
- **Frontend**: React Native (Expo), TypeScript.
- **AI/LLM**: Google Gemini / OpenAI (Configurable).
- **Communication**: WebSocket (Control/Telemetry), MAVLink (Drone).

## 🚀 Getting Started

### Prerequisites

- **Python 3.8+**
- **Node.js 18+**
- **PX4 Autopilot** (Hardware or SITL Simulation)

### 1. Backend Setup

```bash
cd backend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure Environment
# Copy .env.example to .env and set your API keys (GEMINI_API_KEY / OPENAI_API_KEY)
cp .env.example .env

# Run the server
python main.py
```

### 2. Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Run the Expo app
npm start
```

### 3. Running with Simulation (SITL)

Before flying real hardware, we recommend testing with PX4 SITL.

```bash
# In a separate terminal (requires PX4-Autopilot setup)
make px4_sitl jmavsim
```

## 🎮 Usage

1. **Connect**: Launch the app and ensure the backend is connected to the drone (or simulation).
2. **Manual Mode**: Use the **Move** (Right) and **Alt/Yaw** (Left) joysticks to fly manually.
3. **AI Agent**: Switch to the **Chat** tab.
   - Type commands like: _"Take off and ascend to 20 meters."_
   - _"Fly a rectangular path 30m by 40m."_
   - Review the generated plan and click **Confirm** to execute.
4. **Map**: View your drone's position and path in real-time.

## 🤝 Contributing

This is currently a personal project. Contributions are not open at this time.

## 📄 License

This project is personal work.
