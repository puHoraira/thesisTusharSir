/**
 * WebSocket client service for LLM Agent chat
 * Handles bidirectional communication with the drone agent
 */

import { SERVER_ENDPOINT, toWebSocketUrl } from '@/config/runtime';

const getWsBaseUrl = (): string => toWebSocketUrl(SERVER_ENDPOINT);

const describeError = (error: unknown): string => {
  if (error instanceof Error) {
    return error.message;
  }
  if (typeof error === "object" && error !== null) {
    // Try to extract meaningful information from the object
    if ("message" in error && typeof error.message === "string") {
      return error.message;
    }
    if ("error" in error && typeof error.error === "string") {
      return error.error;
    }
  }
  return "Unknown error";
};

// Message types from server
export type MessageType =
  | "ai_message"
  | "plan_confirmation"
  | "status_update"
  | "error";

export type AIMessageType =
  | "info"
  | "success"
  | "warning"
  | "error"
  | "planning"
  | "execution"
  | "system";

// Waypoint in a plan
export interface PlanWaypoint {
  id: string;
  description: string;
  action: string;
  position?: {
    lat: number;
    lon: number;
    alt: number;
  };
}

// Plan data for confirmation
export interface PlanData {
  plan_id: string;
  summary: string;
  waypoints: PlanWaypoint[];
  waypoint_count: number;
  estimated_duration?: number;
}

// Chat message structure
export interface ChatMessage {
  id: string;
  type: MessageType;
  role: "user" | "assistant" | "system";
  content: string;
  messageType?: AIMessageType;
  timestamp: string;
  sessionId?: string;
  planData?: PlanData;
  isLoading?: boolean;
}

// Server message structure
export interface ServerMessage {
  type: MessageType;
  role?: "user" | "assistant";
  content?: string;
  message_type?: AIMessageType;
  timestamp: string;
  session_id?: string;
  plan_data?: PlanData;
  status?: string;
  message?: string;
  error?: string;
  data?: Record<string, unknown>;
}

// Outgoing message types
export interface UserCommandMessage {
  type: "user_command";
  content: string;
}

export interface PlanConfirmMessage {
  type: "plan_confirm";
  plan_id: string;
}

export interface PlanAbortMessage {
  type: "plan_abort";
  plan_id: string;
  feedback?: string;
}

export interface EmergencyStopMessage {
  type: "emergency_stop";
}

export interface GetStatusMessage {
  type: "get_status";
}

export interface ClearHistoryMessage {
  type: "clear_history";
}

export type OutgoingMessage =
  | UserCommandMessage
  | PlanConfirmMessage
  | PlanAbortMessage
  | EmergencyStopMessage
  | GetStatusMessage
  | ClearHistoryMessage;

// Callbacks
type MessageCallback = (message: ChatMessage) => void;
type ConnectionCallback = (connected: boolean) => void;
type PlanConfirmationCallback = (planData: PlanData) => void;

class AgentWebSocketManager {
  private ws: WebSocket | null = null;
  private reconnectDelay = 1000;
  private maxReconnectDelay = 10000;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private keepAliveTimer: NodeJS.Timeout | null = null;
  private connected = false;
  private sessionId: string | null = null;
  private messageIdCounter = 0;

  // Callbacks
  private messageCallbacks: Set<MessageCallback> = new Set();
  private connectionCallbacks: Set<ConnectionCallback> = new Set();
  private planConfirmationCallbacks: Set<PlanConfirmationCallback> = new Set();

  /**
   * Connect to the agent WebSocket
   */
  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      return;
    }

    try {
      const url = `${getWsBaseUrl()}/ws/agent/chat`;
      console.log(`Connecting to agent WebSocket: ${url}`);
      this.ws = new WebSocket(url);

      this.ws.onopen = () => {
        console.log("Agent WebSocket connected");
        this.connected = true;
        this.reconnectDelay = 1000;
        this.notifyConnectionStatus();
        this.startKeepAlive();
      };

      this.ws.onclose = (event) => {
        console.log(
          `Agent WebSocket closed: code=${event.code} reason=${event.reason || "(none)"}`,
        );
        this.connected = false;
        this.sessionId = null;
        this.notifyConnectionStatus();
        this.scheduleReconnect();
      };

      this.ws.onerror = (error) => {
        console.error(`Agent WebSocket error: ${describeError(error)}`);
        this.connected = false;
        this.notifyConnectionStatus();
      };

      this.ws.onmessage = (event) => {
        try {
          const data: ServerMessage = JSON.parse(event.data);
          this.handleServerMessage(data);
        } catch (e) {
          console.error(`Failed to parse agent message: ${describeError(e)}`);
        }
      };
    } catch (error) {
      console.error(
        `Failed to create agent WebSocket: ${describeError(error)}`,
      );
      this.scheduleReconnect();
    }
  }

  /**
   * Handle incoming server message
   */
  private handleServerMessage(data: ServerMessage): void {
    // Store session ID
    if (data.session_id) {
      this.sessionId = data.session_id;
    }

    // Convert server message to ChatMessage
    const chatMessage: ChatMessage = {
      id: `msg_${++this.messageIdCounter}`,
      type: data.type,
      role: data.role || "assistant",
      content: data.content || data.message || data.error || "",
      messageType: data.message_type,
      timestamp: data.timestamp || new Date().toISOString(),
      sessionId: data.session_id,
      planData: data.plan_data,
    };

    // Handle plan confirmation specially
    if (data.type === "plan_confirmation" && data.plan_data) {
      this.notifyPlanConfirmation(data.plan_data);
    }

    // Notify message callbacks
    this.notifyMessageCallbacks(chatMessage);
  }

  /**
   * Schedule reconnection with exponential backoff
   */
  private scheduleReconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
    }

    this.reconnectTimer = setTimeout(() => {
      console.log(`Attempting to reconnect agent WebSocket...`);
      this.connect();
      this.reconnectDelay = Math.min(
        this.reconnectDelay * 1.5,
        this.maxReconnectDelay,
      );
    }, this.reconnectDelay) as unknown as NodeJS.Timeout;
  }

  /**
   * Keep connection alive with periodic pings
   */
  private startKeepAlive(): void {
    if (this.keepAliveTimer) {
      clearInterval(this.keepAliveTimer);
    }

    this.keepAliveTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.sendMessage({ type: "get_status" });
      }
    }, 30000) as unknown as NodeJS.Timeout;
  }

  /**
   * Send a message to the server
   */
  sendMessage(message: OutgoingMessage): boolean {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn("Agent WebSocket not connected");
      return false;
    }

    try {
      this.ws.send(JSON.stringify(message));
      return true;
    } catch (error) {
      console.error(`Failed to send message: ${describeError(error)}`);
      return false;
    }
  }

  /**
   * Send a user command
   */
  sendCommand(content: string): boolean {
    return this.sendMessage({
      type: "user_command",
      content,
    });
  }

  /**
   * Confirm a plan
   */
  confirmPlan(planId: string): boolean {
    return this.sendMessage({
      type: "plan_confirm",
      plan_id: planId,
    });
  }

  /**
   * Abort a plan
   */
  abortPlan(planId: string, feedback?: string): boolean {
    return this.sendMessage({
      type: "plan_abort",
      plan_id: planId,
      feedback,
    });
  }

  /**
   * Send emergency stop
   */
  emergencyStop(): boolean {
    return this.sendMessage({
      type: "emergency_stop",
    });
  }

  /**
   * Clear conversation history
   */
  clearHistory(): boolean {
    const message: ClearHistoryMessage = {
      type: "clear_history",
    };
    return this.sendMessage(message);
  }

  // ========== Subscription Methods ==========

  /**
   * Subscribe to chat messages
   */
  onMessage(callback: MessageCallback): () => void {
    this.messageCallbacks.add(callback);
    return () => {
      this.messageCallbacks.delete(callback);
    };
  }

  /**
   * Subscribe to connection status changes
   * Immediately calls the callback with the current connection state
   */
  onConnectionChange(callback: ConnectionCallback): () => void {
    // Immediately notify with current state
    callback(this.connected);
    // Then add to callbacks for future changes
    this.connectionCallbacks.add(callback);
    return () => {
      this.connectionCallbacks.delete(callback);
    };
  }

  /**
   * Subscribe to plan confirmation requests
   */
  onPlanConfirmation(callback: PlanConfirmationCallback): () => void {
    this.planConfirmationCallbacks.add(callback);
    return () => {
      this.planConfirmationCallbacks.delete(callback);
    };
  }

  // ========== Notification Methods ==========

  private notifyMessageCallbacks(message: ChatMessage): void {
    this.messageCallbacks.forEach((callback) => {
      try {
        callback(message);
      } catch (error) {
        console.error(`Message callback error: ${describeError(error)}`);
      }
    });
  }

  private notifyConnectionStatus(): void {
    this.connectionCallbacks.forEach((callback) => {
      try {
        callback(this.connected);
      } catch (error) {
        console.error(`Connection callback error: ${describeError(error)}`);
      }
    });
  }

  private notifyPlanConfirmation(planData: PlanData): void {
    this.planConfirmationCallbacks.forEach((callback) => {
      try {
        callback(planData);
      } catch (error) {
        console.error(
          `Plan confirmation callback error: ${describeError(error)}`,
        );
      }
    });
  }

  // ========== Utility Methods ==========

  /**
   * Check if connected
   */
  isConnected(): boolean {
    return this.connected;
  }

  /**
   * Get current session ID
   */
  getSessionId(): string | null {
    return this.sessionId;
  }

  /**
   * Disconnect from the WebSocket
   */
  disconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    if (this.keepAliveTimer) {
      clearInterval(this.keepAliveTimer);
      this.keepAliveTimer = null;
    }

    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }

    this.connected = false;
    this.sessionId = null;
    this.notifyConnectionStatus();
  }
}

// Singleton instance
export const agentWebSocket = new AgentWebSocketManager();
