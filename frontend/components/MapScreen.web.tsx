import React, { useEffect, useMemo, useState } from 'react';
import { StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Iconify } from 'react-native-iconify';

import CompactTelemetryPanel from '@/components/CompactTelemetryPanel';
import { droneWebSocket } from '@/services/DroneWebSocket';
import { useMapSelection } from '@/contexts/MapSelectionContext';
import { skyBlueTheme } from '@/constants/Colors';

type TelemetryState = {
  latitude?: number;
  longitude?: number;
  altitude?: number;
  flight_mode?: string;
  battery?: number;
};

const DEFAULT_LATITUDE = 47.397742;
const DEFAULT_LONGITUDE = 8.545594;

const toNumber = (value: string, fallback: number) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

export default function MapScreen() {
  const [telemetry, setTelemetry] = useState<TelemetryState>({});
  const [connected, setConnected] = useState(false);
  const { selectedLocation, setSelectedLocation } = useMapSelection();
  const [targetLatitude, setTargetLatitude] = useState('');
  const [targetLongitude, setTargetLongitude] = useState('');

  useEffect(() => {
    droneWebSocket.connectTelemetry();
    const unsubscribeConnection = droneWebSocket.onConnectionChange(setConnected);
    const unsubscribeTelemetry = droneWebSocket.onTelemetry((data) => {
      setTelemetry((previous) => ({
        ...previous,
        latitude: data.position?.latitude ?? previous.latitude,
        longitude: data.position?.longitude ?? previous.longitude,
        altitude: data.position?.altitude_relative ?? previous.altitude,
        flight_mode: data.flight_mode ?? previous.flight_mode,
        battery: data.battery?.percentage ?? previous.battery,
      }));
    });

    return () => {
      unsubscribeConnection();
      unsubscribeTelemetry();
      droneWebSocket.disconnectTelemetryOnly();
    };
  }, []);

  const mapUrl = useMemo(() => {
    const latitude = telemetry.latitude ?? DEFAULT_LATITUDE;
    const longitude = telemetry.longitude ?? DEFAULT_LONGITUDE;
    const delta = 0.025;
    const marker = selectedLocation
      ? `&marker=${selectedLocation.latitude},${selectedLocation.longitude}`
      : `&marker=${latitude},${longitude}`;
    return `https://www.openstreetmap.org/export/embed.html?bbox=${longitude - delta},${latitude - delta},${longitude + delta},${latitude + delta}&layer=mapnik${marker}`;
  }, [telemetry.latitude, telemetry.longitude, selectedLocation]);

  const setTarget = () => {
    const latitude = toNumber(targetLatitude, NaN);
    const longitude = toNumber(targetLongitude, NaN);
    if (Number.isFinite(latitude) && Number.isFinite(longitude) && latitude >= -90 && latitude <= 90 && longitude >= -180 && longitude <= 180) {
      setSelectedLocation({ latitude, longitude });
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <CompactTelemetryPanel telemetry={telemetry} />
        <View style={styles.statusRow}>
          <Iconify icon="mdi:map-marker" size={18} color={skyBlueTheme.accent} />
          <Text style={styles.statusText}>{connected ? 'Web telemetry connected' : 'Waiting for web telemetry'}</Text>
        </View>
        <View style={styles.targetRow}>
          <TextInput
            value={targetLatitude}
            onChangeText={setTargetLatitude}
            placeholder="Latitude"
            placeholderTextColor="#8a98a8"
            keyboardType="numbers-and-punctuation"
            style={styles.input}
          />
          <TextInput
            value={targetLongitude}
            onChangeText={setTargetLongitude}
            placeholder="Longitude"
            placeholderTextColor="#8a98a8"
            keyboardType="numbers-and-punctuation"
            style={styles.input}
          />
          <TouchableOpacity style={styles.targetButton} onPress={setTarget} accessibilityLabel="Set map target">
            <Iconify icon="mdi:map-marker-plus" size={22} color="#FFF" />
          </TouchableOpacity>
          {selectedLocation && (
            <TouchableOpacity style={styles.clearButton} onPress={() => setSelectedLocation(null)} accessibilityLabel="Clear map target">
              <Iconify icon="mdi:map-marker-off" size={22} color="#FFF" />
            </TouchableOpacity>
          )}
        </View>
      </View>

      <View style={styles.mapContainer}>
        {React.createElement('iframe', {
          title: 'Drone map',
          src: mapUrl,
          style: styles.mapFrame,
          loading: 'lazy',
          referrerPolicy: 'no-referrer',
        })}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: skyBlueTheme.background },
  header: {
    backgroundColor: skyBlueTheme.cardBackground,
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderBottomWidth: 1,
    borderBottomColor: skyBlueTheme.border,
  },
  statusRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 8 },
  statusText: { color: skyBlueTheme.text, fontSize: 12 },
  targetRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 10 },
  input: {
    flex: 1,
    minWidth: 0,
    height: 38,
    borderWidth: 1,
    borderColor: skyBlueTheme.border,
    borderRadius: 4,
    paddingHorizontal: 8,
    color: skyBlueTheme.text,
    backgroundColor: skyBlueTheme.background,
  },
  targetButton: { width: 40, height: 38, borderRadius: 4, backgroundColor: skyBlueTheme.accent, alignItems: 'center', justifyContent: 'center' },
  clearButton: { width: 40, height: 38, borderRadius: 4, backgroundColor: skyBlueTheme.danger, alignItems: 'center', justifyContent: 'center' },
  mapContainer: { flex: 1, minHeight: 300 },
  mapFrame: { width: '100%', height: '100%', borderWidth: 0 },
});
