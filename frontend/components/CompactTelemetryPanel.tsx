import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Animated, {
    withSpring,
    useSharedValue,
    useAnimatedReaction,
    runOnJS,
} from 'react-native-reanimated';
import { Iconify } from 'react-native-iconify';
import { skyBlueTheme } from '@/constants/Colors';

interface TelemetryState {
    velocity?: number;
    altitude?: number;
    flight_mode?: string;
    battery?: number;
}

interface CompactTelemetryPanelProps {
    telemetry: TelemetryState;
    style?: object;
}

export default function CompactTelemetryPanel({ telemetry, style }: CompactTelemetryPanelProps) {
    // State for display values
    const [velocityText, setVelocityText] = useState('0.0 m/s');
    const [altitudeText, setAltitudeText] = useState('0.0 m');
    const [batteryText, setBatteryText] = useState('100%');
    const [batteryColor, setBatteryColor] = useState(skyBlueTheme.success);
    const [batteryIcon, setBatteryIcon] = useState('mdi:battery');

    // Animated values for smooth updates
    const velocity = useSharedValue(telemetry.velocity || 0);
    const altitude = useSharedValue(telemetry.altitude || 0);
    const battery = useSharedValue(telemetry.battery || 100);

    // Update animated values when telemetry changes
    useEffect(() => {
        if (telemetry.velocity !== undefined) {
            velocity.value = withSpring(telemetry.velocity);
        }
        if (telemetry.altitude !== undefined) {
            altitude.value = withSpring(telemetry.altitude);
        }
        if (telemetry.battery !== undefined) {
            battery.value = withSpring(telemetry.battery);
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
            const pct = Math.round(value);
            runOnJS(setBatteryText)(`${pct}%`);

            // Determine color and icon
            let color = skyBlueTheme.success;
            let icon = 'mdi:battery';

            if (value > 50) {
                color = skyBlueTheme.success;
                icon = 'mdi:battery-high';
            } else if (value > 20) {
                color = '#FFA500'; // Warning orange
                icon = 'mdi:battery-medium';
            } else {
                color = skyBlueTheme.danger;
                icon = 'mdi:battery-low';
            }

            runOnJS(setBatteryColor)(color);
            runOnJS(setBatteryIcon)(icon);
        }
    );

    return (
        <View style={[styles.telemetryPanel, style]}>
            <View style={styles.telemetryItem}>
                <Iconify icon="mdi:speedometer" size={12} color={skyBlueTheme.accent} />
                <Text style={styles.telemetryValue}>{velocityText}</Text>
            </View>
            <View style={styles.telemetryItem}>
                <Iconify icon="mdi:arrow-up" size={12} color={skyBlueTheme.accent} />
                <Text style={styles.telemetryValue}>{altitudeText}</Text>
            </View>
            <View style={styles.telemetryItem}>
                <Iconify icon="eos-icons:drone" size={12} color={skyBlueTheme.accent} />
                <Text style={styles.telemetryValue}>{telemetry.flight_mode || 'N/A'}</Text>
            </View>
            <View style={styles.telemetryItem}>
                <Iconify icon={batteryIcon} size={12} color={batteryColor} />
                <Text style={styles.telemetryValue}>{batteryText}</Text>
            </View>
        </View>
    );
}

const styles = StyleSheet.create({
    telemetryPanel: {
        flexDirection: 'row',
        justifyContent: 'space-around',
        alignItems: 'center',
        backgroundColor: skyBlueTheme.background,
        paddingVertical: 8,
        paddingHorizontal: 12,
        borderRadius: 8,
        borderWidth: 1,
        borderColor: skyBlueTheme.border,
    },
    telemetryItem: {
        flexDirection: 'row',
        alignItems: 'center',
        gap: 4,
        flex: 1,
        justifyContent: 'center',
    },
    telemetryValue: {
        fontSize: 11,
        fontWeight: '700',
        color: skyBlueTheme.text,
        minWidth: 40,
    },
});
