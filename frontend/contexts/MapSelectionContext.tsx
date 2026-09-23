import React, { createContext, useContext, useState, ReactNode } from 'react';

interface Location {
    latitude: number;
    longitude: number;
}

interface MapSelectionContextType {
    selectedLocation: Location | null;
    setSelectedLocation: (location: Location | null) => void;
    isMapModeActive: boolean;
    setMapModeActive: (active: boolean) => void;
}

const MapSelectionContext = createContext<MapSelectionContextType | undefined>(undefined);

export function MapSelectionProvider({ children }: { children: ReactNode }) {
    const [selectedLocation, setSelectedLocation] = useState<Location | null>(null);
    const [isMapModeActive, setMapModeActive] = useState(false);

    return (
        <MapSelectionContext.Provider
            value={{
                selectedLocation,
                setSelectedLocation,
                isMapModeActive,
                setMapModeActive,
            }}
        >
            {children}
        </MapSelectionContext.Provider>
    );
}

export function useMapSelection() {
    const context = useContext(MapSelectionContext);
    if (context === undefined) {
        throw new Error('useMapSelection must be used within a MapSelectionProvider');
    }
    return context;
}
