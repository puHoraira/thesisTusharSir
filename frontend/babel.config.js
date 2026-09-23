module.exports = function (api) {
  api.cache(true);
  return {
    presets: ["babel-preset-expo"],
    plugins: [
      [
        "react-native-iconify/babel",
        {
          icons: [
            // Drone and AI icons
            "ix:ai",
            "eos-icons:drone",
            // Navigation and movement icons
            "mdi:airplane-takeoff",
            "mdi:rotate-right",
            "mdi:airplane-landing",
            "mdi:map-marker-path",
            "mdi:map-marker", // Map Tab
            "mdi:map-marker-radius", // Map Tool
            "mdi:map-marker-plus", // Web map target
            "mdi:map-marker-off", // Clear Trails
            "mdi:quadcopter", // Drone Marker
            "mdi:crosshairs-gps", // Center Map
            "mdi:close", // Close/Clear
            "ooui:map-trail", // Map Trail
            "mdi:pause-circle",
            "mdi:arrow-up",
            // Control icons
            "mdi:gamepad-variant",
            "mdi:stop-circle",
            "fa:paper-plane",
            "mdi:refresh",
            "mdi:bug", // Debug icon
            // Telemetry icons
            "mdi:speedometer",
            "mdi:battery",
            "mdi:battery-high",
            "mdi:battery-medium",
            "mdi:battery-low",
          ],
        },
      ],
    ],
  };
};
