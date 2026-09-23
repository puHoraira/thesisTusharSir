/**
 * WebSocket client service for drone control and telemetry
 * Handles bidirectional communication with FastAPI backend
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

export interface TelemetryData {
  type: "telemetry" | "battery" | "flight_mode" | "armed";
  timestamp: string;
  position?: {
    latitude: number;
    longitude: number;
    altitude_relative: number;
    altitude_absolute: number;
  };
  velocity?: {
    resultant: number;
  };
  heading?: number; // Yaw/heading in degrees
  battery?: {
    percentage: number;
    voltage: number;
    current: number;
  };
  flight_mode?: string;
  armed?: boolean;
}

export interface ControlCommand {
  type: "takeoff" | "land" | "emergency_stop" | "velocity_body";
  vx?: number;
  vy?: number;
  vz?: number;
  yaw_rate?: number;
  altitude?: number;
}

export interface CommandResponse {
  status: "success" | "error";
  message: string;
}

type TelemetryCallback = (data: TelemetryData) => void;
type ConnectionCallback = (connected: boolean) => void;

class DroneWebSocketManager {
  private controlWs: WebSocket | null = null;
  private telemetryWs: WebSocket | null = null;
  private controlReconnectDelay = 1000;
  private telemetryReconnectDelay = 1000;
  private maxReconnectDelay = 5000;
  private controlReconnectTimer: NodeJS.Timeout | null = null;
  private telemetryReconnectTimer: NodeJS.Timeout | null = null;
  private telemetryCallbacks: Set<TelemetryCallback> = new Set();
  private connectionCallbacks: Set<ConnectionCallback> = new Set();
  private controlConnected = false;
  private telemetryConnected = false;

  /**
   * Connect to control WebSocket
   */
  connectControl(): void {
    if (this.controlWs?.readyState === WebSocket.OPEN) {
      return;
    }

    try {
      const baseUrl = getWsBaseUrl();
      const url = `${baseUrl}/ws/control`;
      console.log(`Connecting to control WebSocket: ${url}`);
      this.controlWs = new WebSocket(url);

      this.controlWs.onopen = () => {
        this.controlConnected = true;
        this.controlReconnectDelay = 1000;
        this.notifyConnectionStatus();
      };

      this.controlWs.onclose = (event) => {
        console.log(
          `Control WebSocket closed: code=${event.code} reason=${event.reason || "(none)"}`,
        );
        this.controlConnected = false;
        this.notifyConnectionStatus();
        this.scheduleControlReconnect();
      };

      this.controlWs.onerror = (error) => {
        console.error(`Control WebSocket error: ${describeError(error)}`);
        this.controlConnected = false;
        this.notifyConnectionStatus();
      };

      this.controlWs.onmessage = (event) => {
        try {
          const response: CommandResponse = JSON.parse(event.data);
        } catch (e) {
          console.error(
            `Failed to parse control response: ${describeError(e)}`,
          );
        }
      };
    } catch (error) {
      console.error(
        `Failed to create control WebSocket: ${describeError(error)}`,
      );
      this.scheduleControlReconnect();
    }
  }

  /**
   * Connect to telemetry WebSocket
   */
  connectTelemetry(): void {
    if (this.telemetryWs?.readyState === WebSocket.OPEN) {
      return;
    }

    try {
      const url = `${getWsBaseUrl()}/ws/telemetry`;
      this.telemetryWs = new WebSocket(url);

      this.telemetryWs.onopen = () => {
        this.telemetryConnected = true;
        this.telemetryReconnectDelay = 1000;
        this.notifyConnectionStatus();
        // Send ping to keep connection alive
        this.startTelemetryKeepAlive();
      };

      this.telemetryWs.onclose = (event) => {
        console.log(
          `Telemetry WebSocket closed: code=${event.code} reason=${event.reason || "(none)"}`,
        );
        this.telemetryConnected = false;
        this.notifyConnectionStatus();
        this.scheduleTelemetryReconnect();
      };

      this.telemetryWs.onerror = (error) => {
        console.error(`Telemetry WebSocket error: ${describeError(error)}`);
        this.telemetryConnected = false;
        this.notifyConnectionStatus();
      };

      this.telemetryWs.onmessage = (event) => {
        try {
          const data: TelemetryData = JSON.parse(event.data);
          this.notifyTelemetryCallbacks(data);
        } catch (e) {
          console.error(`Failed to parse telemetry data: ${describeError(e)}`);
        }
      };
    } catch (error) {
      console.error(
        `Failed to create telemetry WebSocket: ${describeError(error)}`,
      );
      this.scheduleTelemetryReconnect();
    }
  }

  /**
   * Schedule control WebSocket reconnection with exponential backoff
   */
  private scheduleControlReconnect(): void {
    if (this.controlReconnectTimer) {
      clearTimeout(this.controlReconnectTimer);
    }

    this.controlReconnectTimer = setTimeout(() => {
      this.connectControl();
      this.controlReconnectDelay = Math.min(
        this.controlReconnectDelay * 1.5,
        this.maxReconnectDelay,
      );
    }, this.controlReconnectDelay) as unknown as NodeJS.Timeout;
  }

  /**
   * Schedule telemetry WebSocket reconnection with exponential backoff
   */
  private scheduleTelemetryReconnect(): void {
    if (this.telemetryReconnectTimer) {
      clearTimeout(this.telemetryReconnectTimer);
    }

    this.telemetryReconnectTimer = setTimeout(() => {
      this.connectTelemetry();
      this.telemetryReconnectDelay = Math.min(
        this.telemetryReconnectDelay * 1.5,
        this.maxReconnectDelay,
      );
    }, this.telemetryReconnectDelay) as unknown as NodeJS.Timeout;
  }

  /**
   * Keep telemetry connection alive
   */
  private telemetryKeepAliveTimer: NodeJS.Timeout | null = null;
  private startTelemetryKeepAlive(): void {
    if (this.telemetryKeepAliveTimer) {
      clearInterval(this.telemetryKeepAliveTimer);
    }
    // Send ping every 30 seconds
    this.telemetryKeepAliveTimer = setInterval(() => {
      if (this.telemetryWs?.readyState === WebSocket.OPEN) {
        this.telemetryWs.send(JSON.stringify({ type: "ping" }));
      }
    }, 30000) as unknown as NodeJS.Timeout;
  }

  /**
   * Send control command
   */
  sendCommand(command: ControlCommand): boolean {
    if (!this.controlWs || this.controlWs.readyState !== WebSocket.OPEN) {
      console.warn("Control WebSocket not connected");
      return false;
    }

    try {
      this.controlWs.send(JSON.stringify(command));
      return true;
    } catch (error) {
      console.error(`Failed to send command: ${describeError(error)}`);
      return false;
    }
  }

  /**
   * Subscribe to telemetry updates
   */
  onTelemetry(callback: TelemetryCallback): () => void {
    this.telemetryCallbacks.add(callback);
    return () => {
      this.telemetryCallbacks.delete(callback);
    };
  }

  /**
   * Subscribe to connection status changes
   * Immediately calls the callback with the current connection state
   */
  onConnectionChange(callback: ConnectionCallback): () => void {
    // Immediately notify with current state
    const connected = this.controlConnected && this.telemetryConnected;
    callback(connected);
    // Then add to callbacks for future changes
    this.connectionCallbacks.add(callback);
    return () => {
      this.connectionCallbacks.delete(callback);
    };
  }

  /**
   * Notify all telemetry callbacks
   */
  private notifyTelemetryCallbacks(data: TelemetryData): void {
    this.telemetryCallbacks.forEach((callback) => {
      try {
        callback(data);
      } catch (error) {
        console.error(`Telemetry callback error: ${describeError(error)}`);
      }
    });
  }

  /**
   * Notify all connection status callbacks
   */
  private notifyConnectionStatus(): void {
    const connected = this.controlConnected && this.telemetryConnected;
    this.connectionCallbacks.forEach((callback) => {
      try {
        callback(connected);
      } catch (error) {
        console.error(`Connection callback error: ${describeError(error)}`);
      }
    });
  }

  /**
   * Check if both connections are active
   */
  isConnected(): boolean {
    return this.controlConnected && this.telemetryConnected;
  }

  /**
   * Disconnect only the telemetry WebSocket (for screens that only need telemetry)
   * This preserves the control connection for other screens
   */
  disconnectTelemetryOnly(): void {
    if (this.telemetryReconnectTimer) {
      clearTimeout(this.telemetryReconnectTimer);
      this.telemetryReconnectTimer = null;
    }
    if (this.telemetryKeepAliveTimer) {
      clearInterval(this.telemetryKeepAliveTimer);
      this.telemetryKeepAliveTimer = null;
    }

    if (this.telemetryWs) {
      this.telemetryWs.close();
      this.telemetryWs = null;
    }

    this.telemetryConnected = false;
    // Don't notify connection status change - control may still be connected
  }

  /**
   * Disconnect all WebSocket connections
   */
  disconnect(): void {
    if (this.controlReconnectTimer) {
      clearTimeout(this.controlReconnectTimer);
      this.controlReconnectTimer = null;
    }
    if (this.telemetryReconnectTimer) {
      clearTimeout(this.telemetryReconnectTimer);
      this.telemetryReconnectTimer = null;
    }
    if (this.telemetryKeepAliveTimer) {
      clearInterval(this.telemetryKeepAliveTimer);
      this.telemetryKeepAliveTimer = null;
    }

    if (this.controlWs) {
      this.controlWs.close();
      this.controlWs = null;
    }
    if (this.telemetryWs) {
      this.telemetryWs.close();
      this.telemetryWs = null;
    }

    this.controlConnected = false;
    this.telemetryConnected = false;
    this.notifyConnectionStatus();
  }
}

// Singleton instance
export const droneWebSocket = new DroneWebSocketManager();
