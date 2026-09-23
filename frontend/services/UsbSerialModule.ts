/**
 * USB Serial Native Module
 * This will be implemented as a native Android module
 * For now, provides TypeScript types and runtime stub
 */

import { NativeModules, NativeEventEmitter, Platform } from 'react-native';

interface UsbDevice {
  deviceId: number;
  vendorId: number;
  productId: number;
  deviceName: string;
}

interface UsbSerialInterface {
  listDevices(): Promise<UsbDevice[]>;
  connect(deviceName: string, baudRate: number): Promise<boolean>;
  disconnect(): Promise<void>;
  write(data: string): Promise<boolean>;
}

// Get native module (will be implemented in Android native code)
const { UsbSerial } = NativeModules;

// Create event emitter for data events
const usbSerialEmitter = UsbSerial ? new NativeEventEmitter(UsbSerial) : null;

// Export wrapper
export const UsbSerialModule: UsbSerialInterface = {
  listDevices: async (): Promise<UsbDevice[]> => {
    if (Platform.OS !== 'android' || !UsbSerial) {
      console.warn('USB Serial only supported on Android');
      return [];
    }
    return await UsbSerial.listDevices();
  },

  connect: async (deviceName: string, baudRate: number): Promise<boolean> => {
    if (Platform.OS !== 'android' || !UsbSerial) {
      console.warn('USB Serial only supported on Android');
      return false;
    }
    return await UsbSerial.connect(deviceName, baudRate);
  },

  disconnect: async (): Promise<void> => {
    if (Platform.OS !== 'android' || !UsbSerial) {
      return;
    }
    await UsbSerial.disconnect();
  },

  write: async (data: string): Promise<boolean> => {
    if (Platform.OS !== 'android' || !UsbSerial) {
      console.warn('USB Serial only supported on Android');
      return false;
    }
    return await UsbSerial.write(data);
  },
};

export const addDataListener = (callback: (data: string) => void) => {
  if (!usbSerialEmitter) {
    console.warn('USB Serial events not available');
    return () => {};
  }
  
  const subscription = usbSerialEmitter.addListener('onDataReceived', callback);
  return () => subscription.remove();
};

export default UsbSerialModule;
