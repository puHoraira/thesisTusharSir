# Autonomous UAV Mission Control System

An AI-based UAV mission control system for executing drone missions using natural-language commands.

## Project Structure

```text
thesisTusharSir/
├── frontend/    # Expo frontend
├── backend/     # Python backend
├── .gitignore
└── README.md
```

## First-Time Setup

### Frontend

```bash
cd frontend
npm install
```

Create the required environment file:

```text
frontend/.env
```

Add the required frontend environment variables.

Then start the frontend:

```bash
npx expo start --web
```

### Backend

Create a Python environment:

```bash
cd backend
python -m venv .venv
```

Activate it on Windows:

```powershell
.venv\Scripts\activate
```

Install the dependencies:

```bash
pip install -r requirements.txt
```

Create:

```text
backend/.env
```

and add the required backend environment variables/API keys.

## Run the Project

### 1. Start PX4 SITL

```bash
cd ~/PX4-Autopilot
make px4_sitl gz_x500
```

### 2. Start Backend

```powershell
cd backend
.venv\Scripts\activate
python main.py
```

### 3. Start Frontend

Open another terminal:

```powershell
cd frontend
npx expo start --web
```

## System Flow

```text
User
 ↓
Frontend
 ↓
AI Mission Planner
 ↓
Mission Confirmation
 ↓
Mission Executor
 ↓
MAVSDK / MAVLink
 ↓
PX4
 ↓
Gazebo / UAV
```

## Technologies

* React Native / Expo
* Python
* FastAPI
* LLM
* MAVSDK
* MAVLink
* PX4
* Gazebo

## Environment Files

Do not commit environment files or API keys.

```text
frontend/.env
backend/.env
```

Make sure they are included in `.gitignore`.

## Author

**Abu Horaira Tonmoy**

Department of Computer Science and Engineering
University of Dhaka
