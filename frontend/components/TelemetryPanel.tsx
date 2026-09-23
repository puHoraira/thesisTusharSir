import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Animated, {
  withSpring,
  useSharedValue,
  useAnimatedReaction,
  runOnJS,
} from 'react-native-reanimated';
import { skyBlueTheme } from '@/constants/Colors';

interface TelemetryData {
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

interface TelemetryPanelProps {
  telemetry: TelemetryData;
}

/**
 * Telemetry display panel with animated value updates
 */
export default function TelemetryPanel({ telemetry }: TelemetryPanelProps) {
  // State for display values
  const [velocityText, setVelocityText] = useState('0.0 m/s');
  const [altitudeText, setAltitudeText] = useState('0.0 m');
  const [batteryText, setBatteryText] = useState('100.0%');
  const [latitudeText, setLatitudeText] = useState('0.000000');
  const [longitudeText, setLongitudeText] = useState('0.000000');
  const [batteryColor, setBatteryColor] = useState(skyBlueTheme.success);

  // Animated values for smooth updates
  const velocity = useSharedValue(telemetry.velocity?.resultant || 0);
  const altitude = useSharedValue(telemetry.position?.altitude_relative || 0);
  const battery = useSharedValue(telemetry.battery?.percentage || 100);
  const latitude = useSharedValue(telemetry.position?.latitude || 0);
  const longitude = useSharedValue(telemetry.position?.longitude || 0);

  // Update animated values when telemetry changes
  useEffect(() => {
    if (telemetry.velocity?.resultant !== undefined) {
      velocity.value = withSpring(telemetry.velocity.resultant);
    }
    if (telemetry.position?.altitude_relative !== undefined) {
      altitude.value = withSpring(telemetry.position.altitude_relative);
    }
    if (telemetry.battery?.percentage !== undefined) {
      battery.value = withSpring(telemetry.battery.percentage);
    }
    if (telemetry.position?.latitude !== undefined) {
      latitude.value = withSpring(telemetry.position.latitude);
    }
    if (telemetry.position?.longitude !== undefined) {
      longitude.value = withSpring(telemetry.position.longitude);
    }
  }, [telemetry]);

  // Sync animated values to display text
  useAnimatedReaction(
    () => velocity.value,
    (value) => {
      runOnJS(setVelocityText)(`${value.toFixed(1)} m/s`);
    }
  );

  useAnimatedReaction(
    () => altitude.value,
    (value) => {
      runOnJS(setAltitudeText)(`${value.toFixed(1)} m`);
    }
  );

  useAnimatedReaction(
    () => battery.value,
    (value) => {
      runOnJS(setBatteryText)(`${value.toFixed(1)}%`);
      // Update battery color
      if (value >= 50) {
        runOnJS(setBatteryColor)(skyBlueTheme.success);
      } else if (value > 20) {
        runOnJS(setBatteryColor)(skyBlueTheme.warning);
      } else {
        runOnJS(setBatteryColor)(skyBlueTheme.danger);
      }
    }
  );

  useAnimatedReaction(
    () => latitude.value,
    (value) => {
      runOnJS(setLatitudeText)(value.toFixed(6));
    }
  );

  useAnimatedReaction(
    () => longitude.value,
    (value) => {
      runOnJS(setLongitudeText)(value.toFixed(6));
    }
  );

  return (
    <View style={styles.container}>
      {/* Row 1: Velocity and Altitude */}
      <View style={styles.row}>
        <View style={styles.card}>
          <Text style={styles.label}>Velocity</Text>
          <Text style={styles.value}>
            {velocityText}
          </Text>
        </View>

        <View style={styles.card}>
          <Text style={styles.label}>Altitude</Text>
          <Text style={styles.value}>
            {altitudeText}
          </Text>
        </View>
      </View>

      {/* Row 2: Battery and Mode */}
      <View style={styles.row}>
        <View style={styles.card}>
          <Text style={styles.label}>Battery</Text>
          <Text style={[styles.value, { color: batteryColor }]}>
            {batteryText}
          </Text>
        </View>

        <View style={styles.card}>
          <Text style={styles.label}>Mode</Text>
          <Text style={styles.value}>
            {telemetry.flight_mode || 'Unknown'}
          </Text>
        </View>
      </View>

      {/* Row 3: GPS */}
      <View style={styles.row}>
        <View style={[styles.card, styles.cardFull, styles.gpsCard]}>
          <View style={styles.gpsHeader}>
            <Text style={styles.label}>GPS</Text>
          </View>
          <View style={styles.gpsContainer}>
            <View style={styles.gpsItem}>
              <Text style={styles.gpsLabel}>LAT</Text>
              <Text style={styles.gpsValue}>{latitudeText}°</Text>
            </View>
            <View style={styles.gpsDivider} />
            <View style={styles.gpsItem}>
              <Text style={styles.gpsLabel}>LON</Text>
              <Text style={styles.gpsValue}>{longitudeText}°</Text>
            </View>
          </View>
        </View>
      </View>

      {/* Armed Status */}
      <View style={styles.armedContainer}>
        <View
          style={[
            styles.armedIndicator,
            telemetry.armed
              ? { backgroundColor: skyBlueTheme.success }
              : { backgroundColor: skyBlueTheme.danger },
          ]}
        />
        <Text style={styles.armedText}>
          {telemetry.armed ? 'ARMED' : 'DISARMED'}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    padding: 10,
    backgroundColor: skyBlueTheme.cardBackground,
    borderRadius: 12,
    marginHorizontal: 16,
    marginTop: 16,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  row: {
    flexDirection: 'row',
    marginBottom: 6,
    gap: 6,
  },
  card: {
    flex: 1,
    backgroundColor: skyBlueTheme.background,
    padding: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: skyBlueTheme.border,
  },
  cardFull: {
    flex: 1,
  },
  label: {
    fontSize: 10,
    fontWeight: '600',
    color: skyBlueTheme.text,
    opacity: 0.7,
    marginBottom: 3,
    textTransform: 'uppercase',
  },
  value: {
    fontSize: 16,
    fontWeight: '700',
    color: skyBlueTheme.text,
  },
  gpsCard: {
    paddingVertical: 8,
    paddingHorizontal: 12,
  },
  gpsHeader: {
    marginBottom: 6,
  },
  gpsContainer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 8,
  },
  gpsItem: {
    flex: 1,
    alignItems: 'center',
  },
  gpsLabel: {
    fontSize: 9,
    fontWeight: '700',
    color: skyBlueTheme.text,
    opacity: 0.5,
    marginBottom: 4,
    letterSpacing: 0.5,
  },
  gpsValue: {
    fontSize: 13,
    fontWeight: '600',
    color: skyBlueTheme.accent,
    fontFamily: 'monospace',
  },
  gpsDivider: {
    width: 1,
    height: 30,
    backgroundColor: skyBlueTheme.border,
    marginHorizontal: 12,
  },
  armedContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 6,
    paddingTop: 6,
    borderTopWidth: 1,
    borderTopColor: skyBlueTheme.border,
  },
  armedIndicator: {
    width: 10,
    height: 10,
    borderRadius: 5,
    marginRight: 6,
  },
  armedText: {
    fontSize: 12,
    fontWeight: '700',
    color: skyBlueTheme.text,
    letterSpacing: 1,
  },
});

