import React, { useEffect, useState, useRef } from 'react';
import { StyleSheet, View, Text, TouchableOpacity, Dimensions } from 'react-native';
import MapView, { Marker, Polyline, PROVIDER_DEFAULT, MapPressEvent, Region, AnimatedRegion } from 'react-native-maps';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Iconify } from 'react-native-iconify';


import CompactTelemetryPanel from '@/components/CompactTelemetryPanel';
import { serialComm } from '@/services/SerialCommunication';
import { droneWebSocket } from '@/services/DroneWebSocket';
import { IS_WEB_MODE } from '@/config/runtime';
import { useMapSelection } from '@/contexts/MapSelectionContext';
import { useDroneTrail } from '@/contexts/DroneTrailContext';
import { skyBlueTheme } from '@/constants/Colors';

const { width, height } = Dimensions.get('window');

// Default region if no location is available (e.g., ETH Zurich / PX4 default)
const DEFAULT_REGION: Region = {
    latitude: 47.397742,
    longitude: 8.545594,
    latitudeDelta: 0.005,
    longitudeDelta: 0.005,
};

interface TelemetryState {
    latitude?: number;
    longitude?: number;
    rotation?: number;  // compass heading
    velocity?: number;
    altitude?: number;
    flight_mode?: string;
    battery?: number;
}

export default function MapScreen() {
    const mapRef = useRef<MapView>(null);
    const [telemetry, setTelemetry] = useState<TelemetryState>({});
    const [connected, setConnected] = useState(false);
    // Animated coordinate for smooth marker movement
    const [droneCoordinate] = useState(new AnimatedRegion({
        latitude: DEFAULT_REGION.latitude,
        longitude: DEFAULT_REGION.longitude,
        latitudeDelta: 0,
        longitudeDelta: 0,
    }));
    const { selectedLocation, setSelectedLocation } = useMapSelection();
    const { segments, activeSegment, clearAllTrails } = useDroneTrail();
    const [hasCenteredOnce, setHasCenteredOnce] = useState(false);
    const [userInteracting, setUserInteracting] = useState(false);

    // Connect to serial on mount
    useEffect(() => {
        if (IS_WEB_MODE) {
            droneWebSocket.connectTelemetry();
            const unsubscribeConnection = droneWebSocket.onConnectionChange(setConnected);
            const unsubscribeTelemetry = droneWebSocket.onTelemetry((data) => {
                setTelemetry((prev) => ({
                    ...prev,
                    latitude: data.position?.latitude ?? prev.latitude,
                    longitude: data.position?.longitude ?? prev.longitude,
                    altitude: data.position?.altitude_relative ?? prev.altitude,
                    velocity: data.velocity?.resultant ?? prev.velocity,
                    flight_mode: data.flight_mode ?? prev.flight_mode,
                    battery: data.battery?.percentage ?? prev.battery,
                }));
            });

            return () => {
                unsubscribeConnection();
                unsubscribeTelemetry();
                droneWebSocket.disconnectTelemetryOnly();
            };
        }

        serialComm.connect();

        // Subscribe to connection status
        const unsubscribeConnection = serialComm.onConnectionChange((isConnected) => {
            setConnected(isConnected);
        });

        // Subscribe to telemetry
        const unsubscribeTelemetry = serialComm.onTelemetry((data) => {
            setTelemetry((prev) => {
                const updated = { ...prev };

                if (data.position) {
                    updated.latitude = data.position.latitude;
                    updated.longitude = data.position.longitude;
                    updated.altitude = data.position.altitude_relative;

                    // Animate marker to new position
                    // @ts-ignore: AnimatedRegion timing types are mismatching in this version
                    droneCoordinate.timing({
                        latitude: data.position.latitude,
                        longitude: data.position.longitude,
                        latitudeDelta: 0.005,
                        longitudeDelta: 0.005,
                        duration: 200,
                        useNativeDriver: false,
                    } as any).start();
                }
                if (data.velocity) {
                    updated.velocity = data.velocity.resultant;
                }
                if (data.heading !== undefined) {
                    updated.rotation = data.heading;
                }
                if (data.flight_mode) {
                    updated.flight_mode = data.flight_mode;
                }
                if (data.battery) {
                    updated.battery = data.battery.percentage;
                }

                return updated;
            });
        });

        return () => {
            unsubscribeConnection();
            unsubscribeTelemetry();
            serialComm.disconnect();
        };
    }, []);

    // Auto-center map on drone position initially
    useEffect(() => {
        if (!hasCenteredOnce && telemetry.latitude && telemetry.longitude && !userInteracting) {
            mapRef.current?.animateToRegion({
                latitude: telemetry.latitude,
                longitude: telemetry.longitude,
                latitudeDelta: 0.005,
                longitudeDelta: 0.005,
            }, 1000);
            setHasCenteredOnce(true);
        }
    }, [telemetry.latitude, telemetry.longitude, hasCenteredOnce, userInteracting]);

    const handleMapPress = (e: MapPressEvent) => {
        const { coordinate } = e.nativeEvent;
        setSelectedLocation({
            latitude: coordinate.latitude,
            longitude: coordinate.longitude,
        });
    };

    const handleCenterOnDrone = () => {
        if (telemetry.latitude && telemetry.longitude) {
            mapRef.current?.animateToRegion({
                latitude: telemetry.latitude,
                longitude: telemetry.longitude,
                latitudeDelta: 0.005,
                longitudeDelta: 0.005,
            }, 500);
        }
    };

    const handleClearSelection = () => {
        setSelectedLocation(null);
    };

    return (
        <SafeAreaView style={styles.container} edges={['top']}>
            {/* Header */}
            <View style={styles.header}>
                {/* Telemetry Panel */}
                <View style={{ marginTop: 4 }}>
                    <CompactTelemetryPanel telemetry={telemetry} />
                </View>

                {/* Coordinates Section */}
                <View style={styles.coordinatesContainer}>
                    {/* Drone Position */}
                    <View style={styles.coordinateGroup}>
                        <Iconify icon="eos-icons:drone" size={16} color={skyBlueTheme.accent} />
                        <View style={styles.coordinateBox}>
                            <Text style={styles.coordinateValue}>
                                {telemetry.latitude !== undefined ? `${telemetry.latitude.toFixed(6)},` : '—'}
                            </Text>
                            <Text style={styles.coordinateValue}>
                                {telemetry.longitude !== undefined ? `${telemetry.longitude.toFixed(6)}` : '—'}
                            </Text>
                        </View>
                    </View>

                    {/* Destination */}
                    <View style={styles.coordinateGroup}>
                        <Iconify icon="mdi:map-marker" size={16} color={skyBlueTheme.danger} />
                        <View style={styles.coordinateBox}>
                            <Text style={styles.coordinateValue}>
                                {selectedLocation ? `${selectedLocation.latitude.toFixed(6)},` : '—'}
                            </Text>
                            <Text style={styles.coordinateValue}>
                                {selectedLocation ? `${selectedLocation.longitude.toFixed(6)}` : '—'}
                            </Text>
                        </View>
                    </View>
                </View>
            </View>

            {/* Map Container */}
            <View style={styles.mapContainer}>
                <MapView
                    ref={mapRef}
                    style={styles.map}
                    provider={PROVIDER_DEFAULT}
                    initialRegion={DEFAULT_REGION}
                    onPress={handleMapPress}
                    onPanDrag={() => setUserInteracting(true)}
                    moveOnMarkerPress={false}
                    rotateEnabled={true}
                    showsUserLocation={true}
                    showsCompass={true}
                    showsScale={true}
                >
                    {/* Drone Marker */}
                    {telemetry.latitude !== undefined && telemetry.longitude !== undefined && (
                        <Marker.Animated
                            // @ts-ignore: AnimatedRegion type mismatch with Marker coordinate
                            coordinate={droneCoordinate as any}
                            anchor={{ x: 0.5, y: 0.5 }}
                            title='Drone'
                            description={`${telemetry.latitude.toFixed(6)}, ${telemetry.longitude.toFixed(6)}`}
                            rotation={telemetry.rotation || 0}
                            image={require('../../assets/images/drone_icon.png')}
                        />
                    )}

                    {/* Selected Location Marker */}
                    {selectedLocation && (
                        <Marker
                            coordinate={selectedLocation}
                            draggable
                            onDragEnd={(e) => {
                                const { coordinate } = e.nativeEvent;
                                setSelectedLocation({
                                    latitude: coordinate.latitude,
                                    longitude: coordinate.longitude,
                                });
                            }}
                            pinColor={skyBlueTheme.danger}
                            title="Selected Target"
                            description={`${selectedLocation.latitude.toFixed(6)}, ${selectedLocation.longitude.toFixed(6)}`}
                        />
                    )}

                    {/* Completed Trail Segments */}
                    {segments.map((segment) => (
                        segment.points.length >= 2 && (
                            <Polyline
                                key={segment.id}
                                coordinates={segment.points.map(p => ({
                                    latitude: p.latitude,
                                    longitude: p.longitude,
                                }))}
                                strokeColor={segment.color}
                                strokeWidth={3}
                                lineDashPattern={[0]}
                            />
                        )
                    ))}

                    {/* Active Trail Segment (currently recording) */}
                    {activeSegment && activeSegment.points.length >= 2 && (
                        <Polyline
                            coordinates={activeSegment.points.map(p => ({
                                latitude: p.latitude,
                                longitude: p.longitude,
                            }))}
                            strokeColor={activeSegment.color}
                            strokeWidth={3}
                            lineDashPattern={[5, 5]}  // Dashed line for active segment
                        />
                    )}
                </MapView>

                {/* Map Controls */}
                <View style={styles.controlsContainer}>
                    {selectedLocation && (
                        <TouchableOpacity
                            style={[styles.button, styles.clearButton]}
                            onPress={handleClearSelection}
                        >
                            <Iconify icon="mdi:map-marker-off" size={24} color="#FFF" />
                        </TouchableOpacity>
                    )}

                    {/* Clear Trails Button */}
                    {(segments.length > 0 || (activeSegment && activeSegment.points.length > 0)) && (
                        <TouchableOpacity
                            style={[styles.button, styles.clearButton]}
                            onPress={clearAllTrails}
                        >
                            <Iconify icon="ooui:map-trail" size={24} color="#FFF" />
                        </TouchableOpacity>
                    )}

                    <TouchableOpacity
                        style={[styles.button, styles.centerButton]}
                        onPress={handleCenterOnDrone}
                        disabled={!telemetry.latitude}
                    >
                        <Iconify icon="mdi:crosshairs-gps" size={24} color={telemetry.latitude ? skyBlueTheme.accent : '#CCC'} />
                    </TouchableOpacity>
                </View>
            </View>
        </SafeAreaView>
    );
}

const styles = StyleSheet.create({
    container: {
        flex: 1,
        backgroundColor: skyBlueTheme.background,
    },
    header: {
        backgroundColor: skyBlueTheme.cardBackground,
        paddingVertical: 12,
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
        justifyContent: 'space-between',
        alignItems: 'center',
    },
    titleContainer: {
        flexDirection: 'row',
        alignItems: 'center',
        gap: 8,
    },
    title: {
        fontSize: 18,
        fontWeight: '700',
        color: skyBlueTheme.text,
    },
    statusIndicator: {
        width: 10,
        height: 10,
        borderRadius: 5,
        marginLeft: 4,
    },
    coordinatesContainer: {
        marginTop: 10,
        flexDirection: 'row',
        justifyContent: 'space-around',
        alignItems: 'center',
        width: '100%',
        paddingHorizontal: 1,
    },
    coordinateGroup: {
        flexDirection: 'row',
        alignItems: 'center',
        gap: 3,
    },
    coordinateBox: {
        backgroundColor: skyBlueTheme.background,
        borderRadius: 4,
        paddingHorizontal: 4,
        paddingVertical: 3,
        flexDirection: 'row',
        gap: 4,
        alignItems: 'center',
    },
    coordinateValue: {
        fontSize: 11,
        fontWeight: '500',
        color: skyBlueTheme.text,
        fontFamily: 'monospace',
    },
    mapContainer: {
        flex: 1,
        position: 'relative',
    },
    map: {
        width: '100%',
        height: '100%',
    },
    controlsContainer: {
        position: 'absolute',
        bottom: 30,
        right: 20,
        gap: 16,
        alignItems: 'center',
    },
    button: {
        width: 50,
        height: 50,
        borderRadius: 25,
        backgroundColor: '#FFF',
        justifyContent: 'center',
        alignItems: 'center',
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.2,
        shadowRadius: 4,
        elevation: 5,
    },
    centerButton: {
        backgroundColor: '#FFF',
    },
    clearButton: {
        backgroundColor: skyBlueTheme.danger,
    },
});
