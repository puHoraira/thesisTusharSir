import React from 'react';
import { Iconify } from 'react-native-iconify';
import { Tabs } from 'expo-router';
import { MapSelectionProvider } from '@/contexts/MapSelectionContext';
import { DroneTrailProvider } from '@/contexts/DroneTrailContext';
import { skyBlueTheme } from '@/constants/Colors';
import { useColorScheme } from '@/components/useColorScheme';

// Iconify provides a vast collection of icons including drone and AI icons
function TabBarIcon(props: {
  icon: string;
  color: string;
}) {
  return <Iconify icon={props.icon} size={24} style={{ marginBottom: -3 }} color={props.color} />;
}

export default function TabLayout() {
  const colorScheme = useColorScheme();

  return (
    <MapSelectionProvider>
      <DroneTrailProvider>
        <Tabs
          screenOptions={{
            tabBarActiveTintColor: skyBlueTheme.accent,
            tabBarInactiveTintColor: '#999',
            tabBarStyle: {
              backgroundColor: skyBlueTheme.cardBackground,
              borderTopColor: skyBlueTheme.cardBackground,
              borderTopWidth: 0,
              paddingTop: 8,
              paddingBottom: 8,
              height: 60,
            },
            tabBarLabelStyle: {
              fontSize: 12,
              fontWeight: '600',
            },
            headerShown: false,
          }}>
          <Tabs.Screen
            name="index"
            options={{
              title: 'Manual Control',
              tabBarIcon: ({ color }) => <TabBarIcon icon="mdi:gamepad-variant" color={color} />,
            }}
          />
          <Tabs.Screen
            name="map"
            options={{
              title: 'Map',
              tabBarIcon: ({ color }) => <TabBarIcon icon="mdi:map-marker" color={color} />,
            }}
          />
          <Tabs.Screen
            name="chat"
            options={{
              title: 'AI Agent',
              tabBarIcon: ({ color }) => <TabBarIcon icon="ix:ai" color={color} />,
            }}
          />
          <Tabs.Screen
            name="debug"
            options={{
              title: 'Debug',
              tabBarIcon: ({ color }) => <TabBarIcon icon="mdi:bug" color={color} />,
            }}
          />
        </Tabs>
      </DroneTrailProvider>
    </MapSelectionProvider>
  );
}
