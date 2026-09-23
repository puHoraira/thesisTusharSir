import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  StyleSheet,
  View,
  Text,
  TouchableOpacity,
  ScrollView,
  Dimensions,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { Iconify } from 'react-native-iconify';
import Joystick from '@/components/Joystick';
import TelemetryPanel from '@/components/TelemetryPanel';
import Dialog, { DialogButton } from '@/components/Dialog';
import { serialComm } from '@/services/SerialCommunication';
import { droneWebSocket } from '@/services/DroneWebSocket';
import { skyBlueTheme } from '@/constants/Colors';

const { width: SCREEN_WIDTH, height: SCREEN_HEIGHT } = Dimensions.get('window');

interface TelemetryState {
  position?: {
    latitude: number;
    longitude: number;
    altitude_relative: number;
    altitude_absolute: number;
  };
  velocity?: {
    resultant: number;
  };
  battery?: {
    percentage: number;
    voltage: number;
    current: number;
  };
  flight_mode?: string;
  armed?: boolean;
}

export default function DroneControlScreen() {
  const [connected, setConnected] = useState(false);
  const [telemetry, setTelemetry] = useState<TelemetryState>({});
  const [leftJoystickValues, setLeftJoystickValues] = useState({ x: 0, y: 0 });
  const [rightJoystickValues, setRightJoystickValues] = useState({ x: 0, y: 0 });
  const blockCommandsRef = useRef(false);
  const [dialog, setDialog] = useState<{
    visible: boolean;
    title: string;
    message: string;
    buttons: DialogButton[];
  }>({
    visible: false,
    title: '',
    message: '',
    buttons: [],
  });

  // Connect to WebSocket on mount (for simulation over WiFi)
  useEffect(() => {
    console.log('[INDEX] Connecting via WebSocket...');
    
    // Connect to control and telemetry WebSockets
    droneWebSocket.connectControl();
    droneWebSocket.connectTelemetry();

    // Subscribe to connection status
    const unsubscribeConnection = droneWebSocket.onConnectionChange((isConnected) => {
      console.log(`[INDEX] Connection status changed: ${isConnected}`);
      setConnected(isConnected);
    });

    // Subscribe to telemetry updates
    const unsubscribeTelemetry = droneWebSocket.onTelemetry((data) => {
      setTelemetry((prev) => {
        const updated: TelemetryState = { ...prev };

        // WebSocket telemetry comes as a single combined message
        if (data.position) {
          updated.position = data.position;
        }
        if (data.velocity) {
          updated.velocity = data.velocity;
        }
        if (data.battery) {
          updated.battery = data.battery;
        }
        if (data.flight_mode) {
          updated.flight_mode = data.flight_mode;
        }
        if (data.armed !== undefined) {
          updated.armed = data.armed;
        }

        return updated;
      });
    });

    // Cleanup on unmount
    return () => {
      unsubscribeConnection();
      unsubscribeTelemetry();
      droneWebSocket.disconnect();
    };
  }, []);

  // Re-enable commands when drone enters OFFBOARD mode
  useEffect(() => {
    if (telemetry.flight_mode === 'OFFBOARD' && telemetry.armed) {
      blockCommandsRef.current = false;
    }
  }, [telemetry.flight_mode, telemetry.armed]);

  // Send velocity commands only when drone is ready (armed and in OFFBOARD mode)
  // Backend handles setpoint streaming at 10Hz, frontend just updates target velocity
  useEffect(() => {
    // Block commands if emergency stop or land was just triggered
    if (blockCommandsRef.current) {
      return;
    }

    // Only send commands when ALL conditions are met:
    // 1. Connected to drone
    // 2. Drone is armed (flying) - must be explicitly true
    // 3. Drone is in OFFBOARD mode (accepting velocity commands)
    if (!connected) {
      return;
    }

    if (telemetry.armed !== true) {
      return;
    }

    if (telemetry.flight_mode !== 'OFFBOARD') {
      return;
    }

    // Calculate velocities from joystick values
    const vz = -leftJoystickValues.y;  // Up/Down (inverted: positive y = down in body frame)
    const yawRate = leftJoystickValues.x;  // Yaw rotation
    const vx = rightJoystickValues.y;  // Forward/Backward
    const vy = rightJoystickValues.x;  // Left/Right

    // Send velocity command via WebSocket
    droneWebSocket.sendCommand({
      type: 'velocity_body',
      data: {
        vx: vx,
        vy: vy,
        vz: vz,
        yaw_rate: yawRate,
      }
    });

  }, [connected, telemetry.armed, telemetry.flight_mode, leftJoystickValues, rightJoystickValues]);

  // Control button handlers
  const DEFAULT_TAKEOFF_ALTITUDE = 2.5;

  const handleTakeoff = useCallback(() => {
    console.log(`[INDEX] Takeoff button pressed. Connected: ${connected}`);
    if (!connected) {
      console.warn('[INDEX] Not connected - showing dialog');
      setDialog({
        visible: true,
        title: 'Error',
        message: 'Not connected to drone',
        buttons: [
          {
            text: 'OK',
            onPress: () => { },
            style: 'default',
          },
        ],
      });
      return;
    }
    console.log('[INDEX] Sending takeoff command');
    droneWebSocket.sendCommand({ type: 'takeoff', data: { altitude: DEFAULT_TAKEOFF_ALTITUDE } });
  }, [connected]);

  const handleLand = useCallback(() => {
    if (!connected) {
      setDialog({
        visible: true,
        title: 'Error',
        message: 'Not connected to drone',
        buttons: [
          {
            text: 'OK',
            onPress: () => { },
            style: 'default',
          },
        ],
      });
      return;
    }
    blockCommandsRef.current = true;
    droneWebSocket.sendCommand({ type: 'land', data: {} });
  }, [connected]);

  const handleEmergencyStop = useCallback(() => {
    if (!connected) {
      setDialog({
        visible: true,
        title: 'Error',
        message: 'Not connected to drone',
        buttons: [
          {
            text: 'OK',
            onPress: () => { },
            style: 'default',
          },
        ],
      });
      return;
    }
    setDialog({
      visible: true,
      title: 'Emergency Stop',
      message: 'This will immediately disarm the drone. Are you sure?',
      buttons: [
        {
          text: 'Cancel',
          onPress: () => { },
          style: 'cancel',
        },
        {
          text: 'Stop',
          onPress: () => {
            blockCommandsRef.current = true;
            droneWebSocket.sendCommand({ type: 'emergency_stop', data: {} });
            // Reset joysticks
            setLeftJoystickValues({ x: 0, y: 0 });
            setRightJoystickValues({ x: 0, y: 0 });
          },
          style: 'destructive',
        },
      ],
    });
  }, [connected]);

  return (
    <GestureHandlerRootView style={styles.container}>
      <SafeAreaView style={styles.container} edges={['top']}>
        <ScrollView
          contentContainerStyle={styles.scrollContent}
          showsVerticalScrollIndicator={false}
        >
          {/* Header with connection status */}
          <View style={styles.header}>
            <View style={styles.headerContent}>
              <View style={styles.titleContainer}>
                <Iconify icon="eos-icons:drone" size={20} color={skyBlueTheme.accent} />
                <Text style={styles.title}>Drone Control</Text>
                <View
                  style={[
                    styles.statusIndicator,
                    connected
                      ? { backgroundColor: skyBlueTheme.success }
                      : { backgroundColor: skyBlueTheme.danger },
                  ]}
                />
              </View>
            </View>
          </View>

          {/* Telemetry Panel */}
          <TelemetryPanel telemetry={telemetry} />

          {/* Dual Joystick Layout */}
          <View style={styles.joystickContainer}>
            {/* Left Joystick: Altitude & Yaw */}
            <View style={styles.joystickWrapper}>
              <View style={styles.joystickTitleContainer}>
                <Text style={styles.joystickLabel}>Altitude & Yaw</Text>
              </View>
              <Joystick
                onMove={(x, y) => {
                  setLeftJoystickValues({ x, y });
                }}
                axisLabels={{
                  top: 'U',
                  bottom: 'D',
                  left: 'YL',
                  right: 'YR',
                }}
              />
            </View>

            {/* Right Joystick: Movement */}
            <View style={styles.joystickWrapper}>
              <View style={styles.joystickTitleContainer}>
                <Text style={styles.joystickLabel}>Movement</Text>
              </View>
              <Joystick
                onMove={(x, y) => {
                  setRightJoystickValues({ x, y });
                }}
                axisLabels={{
                  top: 'F',
                  bottom: 'B',
                  left: 'L',
                  right: 'R',
                }}
              />
            </View>
          </View>

          {/* Control Buttons */}
          <View style={styles.controlButtons}>
            <TouchableOpacity
              style={[styles.button, styles.buttonTakeoff]}
              onPress={handleTakeoff}
              disabled={!connected}
            >
              <Text style={styles.buttonText}>Takeoff</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={[styles.button, styles.buttonLand]}
              onPress={handleLand}
              disabled={!connected}
            >
              <Text style={styles.buttonText}>Land</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={[styles.button, styles.buttonEmergency]}
              onPress={handleEmergencyStop}
              disabled={!connected}
            >
              <Text style={[styles.buttonText, styles.buttonEmergencyText]}>
                Emergency Stop
              </Text>
            </TouchableOpacity>
          </View>
        </ScrollView>

        {/* Dialog Modal */}
        <Dialog
          visible={dialog.visible}
          title={dialog.title}
          message={dialog.message}
          buttons={dialog.buttons}
          onClose={() => setDialog({ ...dialog, visible: false })}
        />
      </SafeAreaView>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: skyBlueTheme.background,
  },
  scrollContent: {
    paddingBottom: 32,
  },
  header: {
    backgroundColor: skyBlueTheme.cardBackground,
    paddingVertical: 16,
    paddingHorizontal: 16,
    borderBottomWidth: 1,
    borderBottomColor: skyBlueTheme.border,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.05,
    shadowRadius: 4,
    elevation: 2,
  },
  headerContent: {
    flexDirection: 'row',
    justifyContent: 'flex-start',
    alignItems: 'center',
  },
  titleContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  statusIndicator: {
    width: 10,
    height: 10,
    borderRadius: 5,
    marginLeft: 4,
  },
  title: {
    fontSize: 20,
    fontWeight: '700',
    color: skyBlueTheme.text,
  },
  joystickContainer: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    alignItems: 'flex-start',
    paddingVertical: 24,
    paddingHorizontal: 8,
    gap: 8,
  },
  joystickWrapper: {
    alignItems: 'center',
    gap: 0,
    marginBottom: 16,
    paddingHorizontal: 12,
    width: '50%',
  },
  joystickTitleContainer: {
    marginBottom: 12,
    height: 20,
    justifyContent: 'center',
    alignItems: 'center',
    width: '100%',
  },
  joystickLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: skyBlueTheme.text,
    textAlign: 'center',
  },
  controlButtons: {
    paddingHorizontal: 16,
    gap: 12,
    marginTop: 12,
  },
  button: {
    paddingVertical: 16,
    paddingHorizontal: 24,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 56,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.2,
    shadowRadius: 4,
    elevation: 4,
  },
  buttonTakeoff: {
    backgroundColor: skyBlueTheme.success,
  },
  buttonLand: {
    backgroundColor: skyBlueTheme.accent,
  },
  buttonEmergency: {
    backgroundColor: skyBlueTheme.danger,
  },
  buttonText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#FFFFFF',
    letterSpacing: 0.5,
  },
  buttonEmergencyText: {
    fontSize: 14,
  }
});
