/**
 * USB Serial Communication Service
 * Direct communication with Jetson via telemetry radio
 * 
 * Replaces HTTP/WebSocket with efficient serial protocol
 */

/**
 * USB Serial Communication Service
 * Direct communication with Jetson via telemetry radio
 * 
 * Architecture:
 * Mobile (USB OTG) → Ground Radio (915MHz) → Air Radio → Jetson (/dev/ttyUSB0) → Backend
 */

import uuid from 'react-native-uuid';
import UsbSerialModule, { addDataListener } from './UsbSerialModule';

// ========== Message Types ==========

export interface SerialMessage {
  type: string;
  id?: string;
  timestamp: string;
  data?: any;
}

// Outgoing message types
export interface AgentCommandMessage extends SerialMessage {
  type: 'agent_command';
  data: {
    content: string;
  };
}

export interface ControlCommandMessage extends SerialMessage {
  type: 'control_command';
  data: {
    action: 'takeoff' | 'land' | 'emergency_stop' | 'velocity_body' | 'move_ned';
    altitude?: number;
    vx?: number;
    vy?: number;
    vz?: number;
    yaw_rate?: number;
    north?: number;
    east?: number;
    down?: number;
  };
}

export interface PlanConfirmMessage extends SerialMessage {
  type: 'plan_confirm';
  data: {
    plan_id: string;
  };
}

export interface PlanAbortMessage extends SerialMessage {
  type: 'plan_abort';
  data: {
    plan_id: string;
    feedback?: string;
  };
}

export interface StatusRequestMessage extends SerialMessage {
  type: 'status_request';
}

export interface ClearHistoryMessage extends SerialMessage {
  type: 'clear_history';
}

// Incoming message types
export interface AIMessageData {
  role: 'user' | 'assistant' | 'system';
  content: string;
  message_type?: 'info' | 'success' | 'warning' | 'error' | 'planning' | 'execution' | 'system';
  session_id?: string;
}

export interface PlanData {
  plan_id: string;
  summary: string;
  waypoints: Array<{
    id: string;
    description: string;
    action: string;
    position?: {
      lat: number;
      lon: number;
      alt: number;
    };
  }>;
  waypoint_count: number;
  estimated_duration?: number;
}

export interface TelemetryData {
  position?: {
    latitude: number;
    longitude: number;
    altitude_relative: number;
    altitude_absolute: number;
  };
  velocity?: {
    resultant: number;
  };
  heading?: number;
  battery?: {
    percentage: number;
    voltage: number;
    current?: number;
  };
  flight_mode?: string;
  armed?: boolean;
}

export interface BatteryData {
  percentage: number;
  voltage: number;
  current: number;
}

// Callbacks
type MessageCallback = (message: SerialMessage) => void;
type ConnectionCallback = (connected: boolean) => void;
type TelemetryCallback = (data: TelemetryData) => void;
type BatteryCallback = (data: BatteryData) => void;
type FlightModeCallback = (mode: string) => void;
type ArmedCallback = (armed: boolean) => void;
type AIMessageCallback = (message: any) => void;
type PlanConfirmationCallback = (plan: PlanData) => void;

// ========== Serial Communication Manager ==========

class SerialCommunicationManager {
  private deviceName: string | null = null;
  private connected = false;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private reconnectDelay = 2000;
  private lineBuffer = '';
  private dataListenerCleanup: (() => void) | null = null;

  // Callbacks
  private messageCallbacks: Set<MessageCallback> = new Set();
  private connectionCallbacks: Set<ConnectionCallback> = new Set();
  private telemetryCallbacks: Set<TelemetryCallback> = new Set();
  private batteryCallbacks: Set<BatteryCallback> = new Set();
  private flightModeCallbacks: Set<FlightModeCallback> = new Set();
  private armedCallbacks: Set<ArmedCallback> = new Set();
  private aiMessageCallbacks: Set<AIMessageCallback> = new Set();
  private planConfirmationCallbacks: Set<PlanConfirmationCallback> = new Set();

  // Pending responses (for request/response matching)
  private pendingResponses: Map<string, (response: SerialMessage) => void> = new Map();
  private responseTimeout = 10000; // 10 seconds

  /**
   * List available USB devices
   */
  async listDevices(): Promise<any[]> {
    try {
      return await UsbSerialModule.listDevices();
    } catch (error) {
      console.error('Failed to list USB devices:', error);
      return [];
    }
  }

  /**
   * Connect to USB serial device
   */
  async connect(deviceName?: string): Promise<boolean> {
    try {
      // If no device specified, use first available
      if (!deviceName) {
        const devices = await this.listDevices();
        console.log(`[CONNECT] Found ${devices.length} USB devices`);
        if (devices.length === 0) {
          console.warn('[CONNECT] No USB devices found');
          return false;
        }
        deviceName = devices[0].deviceName;
        console.log(`[CONNECT] Using device: ${deviceName}`);
      }

      console.log(`[CONNECT] Connecting to USB device: ${deviceName} @ 57600 baud`);
      
      const success = await UsbSerialModule.connect(deviceName, 57600);
      
      if (!success) {
        throw new Error('Failed to connect to device');
      }
      
      this.deviceName = deviceName;
      this.connected = true;
      this.lineBuffer = '';
      
      // Setup data listener
      this.dataListenerCleanup = addDataListener(this.handleSerialData);
      
      // Notify connection
      this.notifyConnectionStatus();
      
      console.log('[CONNECT] ✅ Serial connection established');
      return true;
    } catch (error) {
      console.error('Failed to connect to USB device:', error);
      this.connected = false;
      this.notifyConnectionStatus();
      this.scheduleReconnect();
      return false;
    }
  }

  /**
   * Handle incoming serial data
   */
  private handleSerialData = (data: string): void => {
    // Append to line buffer
    this.lineBuffer += data;
    console.log(`[RX] Received ${data.length} chars, buffer: ${this.lineBuffer.length}`);

    // Process complete lines (newline-delimited)
    const lines = this.lineBuffer.split('\n');
    
    // Keep incomplete line in buffer
    this.lineBuffer = lines.pop() || '';

    // Process complete lines
    lines.forEach(line => {
      const trimmed = line.trim();
      if (trimmed) {
        try {
          const message: SerialMessage = JSON.parse(trimmed);
          this.handleMessage(message);
        } catch (error) {
          console.error('Failed to parse serial message:', trimmed, error);
        }
      }
    });
  };

  /**
   * Handle parsed message
   */
  private handleMessage(message: SerialMessage): void {
    // Check for pending response
    if (message.id) {
      const callback = this.pendingResponses.get(message.id);
      if (callback) {
        callback(message);
        this.pendingResponses.delete(message.id);
        return; // Don't process further
      }
    }

    // Route by message type
    switch (message.type) {
      case 'telemetry':
        if (message.data) {
          this.notifyTelemetryCallbacks(message.data);
        }
        break;

      case 'battery':
        if (message.data) {
          this.notifyBatteryCallbacks(message.data);
        }
        break;

      case 'flight_mode':
        if (message.data?.mode) {
          this.notifyFlightModeCallbacks(message.data.mode);
        }
        break;

      case 'armed':
        if (message.data?.armed !== undefined) {
          this.notifyArmedCallbacks(message.data.armed);
        }
        break;

      case 'ai_message':
        // AI messages from agent
        this.notifyAIMessageCallbacks(message);
        break;

      case 'plan_confirmation':
        // Plan confirmation requests
        if (message.data) {
          this.notifyPlanConfirmationCallbacks(message.data);
        }
        break;

      default:
        // Generic message callback (for AI messages, errors, etc.)
        this.notifyMessageCallbacks(message);
        break;
    }
  }

  /**
   * Send message to backend via serial (with chunking for radio stability)
   */
  async sendMessage(message: Omit<SerialMessage, 'timestamp'>): Promise<boolean> {
    console.log(`[SEND_MESSAGE] Step 1: Called with type=${message.type}, connected=${this.connected}`);
    
    if (!this.connected) {
      console.warn('[SEND_MESSAGE] Blocked: Serial not connected');
      return false;
    }

    try {
      console.log(`[SEND_MESSAGE] Step 2: Connection check passed`);
      
      // Add timestamp
      const fullMessage: SerialMessage = {
        ...message,
        timestamp: new Date().toISOString(),
      };
      console.log(`[SEND_MESSAGE] Step 3: Timestamp added`);

      // Serialize to JSON and add newline
      const data = JSON.stringify(fullMessage) + '\n';
      console.log(`[TX] Step 4: Serialized ${data.length} bytes: ${data.substring(0, 80)}...`);
      
      // CRITICAL: Send in small chunks with delays for radio stability
      // Radio buffer is small (~64-128 bytes), sending all at once causes corruption
      const CHUNK_SIZE = 16; // Send 16 bytes at a time
      const CHUNK_DELAY_MS = 30; // 30ms delay between chunks
      
      console.log(`[TX] Step 5: Sending in ${CHUNK_SIZE}-byte chunks with ${CHUNK_DELAY_MS}ms delays...`);
      
      const totalChunks = Math.ceil(data.length / CHUNK_SIZE);
      for (let i = 0; i < data.length; i += CHUNK_SIZE) {
        const chunk = data.substring(i, i + CHUNK_SIZE);
        const chunkNum = Math.floor(i / CHUNK_SIZE) + 1;
        
        console.log(`[TX] Chunk ${chunkNum}/${totalChunks}: ${chunk.length} bytes`);
        
        const success = await UsbSerialModule.write(chunk);
        if (!success) {
          console.error(`[TX] ❌ Chunk ${chunkNum} failed`);
          return false;
        }
        
        // Wait between chunks (let radio transmit)
        if (i + CHUNK_SIZE < data.length) {
          await new Promise(resolve => setTimeout(resolve, CHUNK_DELAY_MS));
        }
      }
      
      console.log('[TX] ✅ All chunks sent successfully');
      return true;
      
    } catch (error) {
      console.error('[SEND_MESSAGE] Exception in sendMessage:');
      console.error('[SEND_MESSAGE] Error type:', typeof error);
      console.error('[SEND_MESSAGE] Error:', error);
      if (error && typeof error === 'object') {
        console.error('[SEND_MESSAGE] Error keys:', Object.keys(error));
        console.error('[SEND_MESSAGE] Error.message:', (error as any).message);
        console.error('[SEND_MESSAGE] Error.code:', (error as any).code);
      }
      this.disconnect();
      return false;
    }
  }

  /**
   * Send message and wait for response
   */
  async sendWithResponse(
    message: Omit<SerialMessage, 'id' | 'timestamp'>,
    timeout: number = this.responseTimeout
  ): Promise<SerialMessage> {
    const id = uuid.v4() as string;
    const fullMessage = { ...message, id };

    return new Promise(async (resolve, reject) => {
      // Setup timeout
      const timer = setTimeout(() => {
        this.pendingResponses.delete(id);
        reject(new Error('Response timeout'));
      }, timeout);

      // Setup response callback
      this.pendingResponses.set(id, (response) => {
        clearTimeout(timer);
        resolve(response);
      });

      // Send message
      const success = await this.sendMessage(fullMessage);
      if (!success) {
        clearTimeout(timer);
        this.pendingResponses.delete(id);
        reject(new Error('Failed to send message'));
      }
    });
  }

  /**
   * Send agent command (AI chat)
   */
  async sendAgentCommand(content: string): Promise<boolean> {
    return this.sendMessage({
      type: 'agent_command',
      id: uuid.v4() as string,
      data: { content },
    });
  }

  /**
   * Send control command
   */
  async sendControlCommand(
    action: 'takeoff' | 'land' | 'emergency_stop' | 'velocity_body' | 'move_ned',
    params?: any
  ): Promise<boolean> {
    try {
      console.log(`[SEND_CONTROL] Step 1: Action=${action}, Connected=${this.connected}`);
      
      let msgId: string;
      try {
        msgId = uuid.v4() as string;
        console.log(`[SEND_CONTROL] Step 2: UUID generated = ${msgId}`);
      } catch (uuidError) {
        console.error(`[SEND_CONTROL] UUID generation failed:`, uuidError);
        throw uuidError;
      }
      
      const message = {
        type: 'control_command',
        id: msgId,
        data: {
          action,
          ...params,
        },
      };
      console.log(`[SEND_CONTROL] Step 3: Message created`);
      
      console.log(`[SEND_CONTROL] Step 4: Calling sendMessage...`);
      const result = await this.sendMessage(message);
      console.log(`[SEND_CONTROL] Step 5: sendMessage returned ${result}`);
      return result;
    } catch (error) {
      console.error(`[SEND_CONTROL] Exception caught:`, error);
      console.error(`[SEND_CONTROL] Error type: ${typeof error}`);
      console.error(`[SEND_CONTROL] Error keys: ${Object.keys(error || {})}`);
      if (error && typeof error === 'object') {
        console.error(`[SEND_CONTROL] Error.message: ${(error as any).message}`);
        console.error(`[SEND_CONTROL] Error.toString: ${error.toString()}`);
      }
      return false;
    }
  }

  /**
   * Confirm plan (same as confirmPlan but for consistency)
   */
  async sendPlanConfirm(planId: string): Promise<boolean> {
    return this.confirmPlan(planId);
  }

  /**
   * Abort plan (same as abortPlan but for consistency)
   */
  async sendPlanAbort(planId: string, feedback?: string): Promise<boolean> {
    return this.abortPlan(planId, feedback);
  }

  /**
   * Clear history (same as clearHistory but for consistency)
   */
  async sendClearHistory(): Promise<boolean> {
    return this.clearHistory();
  }

  /**
   * Confirm plan
   */
  async confirmPlan(planId: string): Promise<boolean> {
    return this.sendMessage({
      type: 'plan_confirm',
      id: uuid.v4() as string,
      data: { plan_id: planId },
    });
  }

  /**
   * Abort plan
   */
  async abortPlan(planId: string, feedback?: string): Promise<boolean> {
    return this.sendMessage({
      type: 'plan_abort',
      id: uuid.v4() as string,
      data: { plan_id: planId, feedback },
    });
  }

  /**
   * Request status
   */
  async requestStatus(): Promise<SerialMessage> {
    return this.sendWithResponse({
      type: 'status_request',
    });
  }

  /**
   * Clear history
   */
  async clearHistory(): Promise<boolean> {
    return this.sendMessage({
      type: 'clear_history',
      id: uuid.v4() as string,
    });
  }

  /**
   * Emergency stop
   */
  async emergencyStop(): Promise<SerialMessage> {
    return this.sendWithResponse({
      type: 'control_command',
      data: { action: 'emergency_stop' },
    }, 5000); // Shorter timeout for emergency
  }

  // ========== Subscription Methods ==========

  onMessage(callback: MessageCallback): () => void {
    this.messageCallbacks.add(callback);
    return () => this.messageCallbacks.delete(callback);
  }

  onConnectionChange(callback: ConnectionCallback): () => void {
    callback(this.connected); // Immediate notification
    this.connectionCallbacks.add(callback);
    return () => this.connectionCallbacks.delete(callback);
  }

  onTelemetry(callback: TelemetryCallback): () => void {
    this.telemetryCallbacks.add(callback);
    return () => this.telemetryCallbacks.delete(callback);
  }

  onBattery(callback: BatteryCallback): () => void {
    this.batteryCallbacks.add(callback);
    return () => this.batteryCallbacks.delete(callback);
  }

  onFlightMode(callback: FlightModeCallback): () => void {
    this.flightModeCallbacks.add(callback);
    return () => this.flightModeCallbacks.delete(callback);
  }

  onArmed(callback: ArmedCallback): () => void {
    this.armedCallbacks.add(callback);
    return () => this.armedCallbacks.delete(callback);
  }

  onAIMessage(callback: AIMessageCallback): () => void {
    this.aiMessageCallbacks.add(callback);
    return () => this.aiMessageCallbacks.delete(callback);
  }

  onPlanConfirmation(callback: PlanConfirmationCallback): () => void {
    this.planConfirmationCallbacks.add(callback);
    return () => this.planConfirmationCallbacks.delete(callback);
  }

  // ========== Notification Methods ==========

  private notifyMessageCallbacks(message: SerialMessage): void {
    this.messageCallbacks.forEach(callback => {
      try {
        callback(message);
      } catch (error) {
        console.error('Message callback error:', error);
      }
    });
  }

  private notifyConnectionStatus(): void {
    this.connectionCallbacks.forEach(callback => {
      try {
        callback(this.connected);
      } catch (error) {
        console.error('Connection callback error:', error);
      }
    });
  }

  private notifyTelemetryCallbacks(data: TelemetryData): void {
    this.telemetryCallbacks.forEach(callback => {
      try {
        callback(data);
      } catch (error) {
        console.error('Telemetry callback error:', error);
      }
    });
  }

  private notifyBatteryCallbacks(data: BatteryData): void {
    this.batteryCallbacks.forEach(callback => {
      try {
        callback(data);
      } catch (error) {
        console.error('Battery callback error:', error);
      }
    });
  }

  private notifyFlightModeCallbacks(mode: string): void {
    this.flightModeCallbacks.forEach(callback => {
      try {
        callback(mode);
      } catch (error) {
        console.error('Flight mode callback error:', error);
      }
    });
  }

  private notifyArmedCallbacks(armed: boolean): void {
    this.armedCallbacks.forEach(callback => {
      try {
        callback(armed);
      } catch (error) {
        console.error('Armed callback error:', error);
      }
    });
  }

  private notifyAIMessageCallbacks(message: SerialMessage): void {
    this.aiMessageCallbacks.forEach(callback => {
      try {
        callback(message);
      } catch (error) {
        console.error('AI message callback error:', error);
      }
    });
  }

  private notifyPlanConfirmationCallbacks(plan: PlanData): void {
    this.planConfirmationCallbacks.forEach(callback => {
      try {
        callback(plan);
      } catch (error) {
        console.error('Plan confirmation callback error:', error);
      }
    });
  }

  // ========== Connection Management ==========

  /**
   * Schedule reconnection
   */
  private scheduleReconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
    }

    this.reconnectTimer = setTimeout(() => {
      console.log('Attempting to reconnect serial...');
      this.connect(this.deviceName || undefined);
    }, this.reconnectDelay) as unknown as NodeJS.Timeout;
  }

  /**
   * Check if connected
   */
  isConnected(): boolean {
    return this.connected;
  }

  /**
   * Disconnect
   */
  async disconnect(): Promise<void> {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    if (this.dataListenerCleanup) {
      this.dataListenerCleanup();
      this.dataListenerCleanup = null;
    }

    try {
      await UsbSerialModule.disconnect();
    } catch (error) {
      console.error('Error during disconnect:', error);
    }

    this.deviceName = null;
    this.connected = false;
    this.lineBuffer = '';
    this.pendingResponses.clear();
    this.notifyConnectionStatus();
  }
}

// Singleton instance
export const serialComm = new SerialCommunicationManager();
