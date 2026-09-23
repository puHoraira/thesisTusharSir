import 'dotenv/config';

export default {
  expo: {
    name: "Drone Control",
    slug: "drone-control",
    version: "1.0.0",
    orientation: "portrait",
    icon: "./assets/images/icon.png",
    scheme: "dronecontrol",
    userInterfaceStyle: "automatic",
    splash: {
      image: "./assets/images/splash-icon.png",
      resizeMode: "contain",
      backgroundColor: "#ffffff"
    },
    android: {
      package: "com.dronecontrol.app",
      versionCode: 1,
      adaptiveIcon: {
        foregroundImage: "./assets/images/adaptive-icon.png",
        backgroundColor: "#ffffff"
      },
      permissions: [
        "android.permission.ACCESS_NETWORK_STATE",
        "android.permission.INTERNET",
        "android.permission.ACCESS_WIFI_STATE",
        "android.hardware.usb.host"
      ]
    },
    plugins: [
      "expo-router"
    ],
    extra: {
      APP_MODE: process.env.APP_MODE || "auto",
      SERVER_ENDPOINT: process.env.SERVER_ENDPOINT || "http://192.168.0.102:8000",
      GOOGLE_MAPS_API_KEY: process.env.GOOGLE_MAPS_API_KEY || "",
      eas: {
        projectId: "9a683ab2-57fd-476b-8970-92709de3d44a"
      }
    }
  }
};
