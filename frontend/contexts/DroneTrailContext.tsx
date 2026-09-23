import React, { createContext, useContext, useState, useCallback, useRef, useEffect } from 'react';
import { serialComm } from '@/services/SerialCommunication';

// Single point on the trail
export interface TrailPoint {
    latitude: number;
    longitude: number;
    timestamp: number;
}

// A segment of trail (one per user command)
export interface TrailSegment {
    id: string;
    points: TrailPoint[];
    color?: string;
    startTime: number;
    endTime?: number;
}

interface DroneTrailContextType {
    // All trail segments
    segments: TrailSegment[];
    // Current active segment (being recorded)
    activeSegment: TrailSegment | null;
    // Start a new trail segment (called when user sends command)
    startNewSegment: () => void;
    // End the current segment (optional, auto-ends on next startNewSegment)
    endCurrentSegment: () => void;
    // Clear all trails
    clearAllTrails: () => void;
    // Sampling interval in ms
    samplingInterval: number;
    setSamplingInterval: (interval: number) => void;
}

const DroneTrailContext = createContext<DroneTrailContextType | undefined>(undefined);

// Trail colors for different segments (cycles through these)
const TRAIL_COLORS = [
    '#3498db', // Blue
    '#e74c3c', // Red
    '#2ecc71', // Green
    '#9b59b6', // Purple
    '#f39c12', // Orange
    '#1abc9c', // Teal
];

interface DroneTrailProviderProps {
    children: React.ReactNode;
}

export function DroneTrailProvider({ children }: DroneTrailProviderProps) {
    const [segments, setSegments] = useState<TrailSegment[]>([]);
    const [activeSegment, setActiveSegment] = useState<TrailSegment | null>(null);
    const [samplingInterval, setSamplingInterval] = useState(400); // 300ms interval
    const lastSampleTime = useRef<number>(0);
    // Track the total number of segments created (including active) for color cycling
    const segmentCount = useRef<number>(0);

    // Generate unique ID for segment
    const generateId = () => `segment_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;

    // Start a new trail segment
    const startNewSegment = useCallback(() => {
        setActiveSegment(prev => {
            // End current segment if exists by adding to segments
            if (prev && prev.points.length > 0) {
                setSegments(segs => [...segs, { ...prev, endTime: Date.now() }]);
            }

            // Get color based on total segments created
            const color = TRAIL_COLORS[segmentCount.current % TRAIL_COLORS.length];
            segmentCount.current += 1;

            // Create new segment
            const newSegment: TrailSegment = {
                id: generateId(),
                points: [],
                color,
                startTime: Date.now(),
            };

            lastSampleTime.current = 0; // Reset sampling timer
            return newSegment;
        });
    }, []);

    // End current segment without starting a new one
    const endCurrentSegment = useCallback(() => {
        setActiveSegment(prev => {
            if (prev && prev.points.length > 0) {
                setSegments(segs => [...segs, { ...prev, endTime: Date.now() }]);
            }
            return null;
        });
    }, []);

    // Clear all trails
    const clearAllTrails = useCallback(() => {
        setSegments([]);
        setActiveSegment(null);
        segmentCount.current = 0;
        lastSampleTime.current = 0;
    }, []);

    // Add point to active segment (with sampling)
    const addPointToActiveSegment = useCallback((latitude: number, longitude: number) => {
        const now = Date.now();

        // Check if enough time has passed since last sample
        if (now - lastSampleTime.current < samplingInterval) {
            return;
        }

        lastSampleTime.current = now;

        if (!activeSegment) {
            return;
        }

        const newPoint: TrailPoint = {
            latitude,
            longitude,
            timestamp: now,
        };

        setActiveSegment(prev => {
            if (!prev) return null;
            return {
                ...prev,
                points: [...prev.points, newPoint],
            };
        });
    }, [activeSegment, samplingInterval]);

    // Subscribe to telemetry for position updates
    useEffect(() => {
        const unsubscribe = serialComm.onTelemetry((data) => {
            if (data.position) {
                addPointToActiveSegment(data.position.latitude, data.position.longitude);
            }
        });

        return () => {
            unsubscribe();
        };
    }, [addPointToActiveSegment]);

    const value: DroneTrailContextType = {
        segments,
        activeSegment,
        startNewSegment,
        endCurrentSegment,
        clearAllTrails,
        samplingInterval,
        setSamplingInterval,
    };

    return (
        <DroneTrailContext.Provider value={value}>
            {children}
        </DroneTrailContext.Provider>
    );
}

export function useDroneTrail() {
    const context = useContext(DroneTrailContext);
    if (context === undefined) {
        throw new Error('useDroneTrail must be used within a DroneTrailProvider');
    }
    return context;
}
