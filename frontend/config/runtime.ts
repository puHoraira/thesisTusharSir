import { Platform } from 'react-native';
import Constants from 'expo-constants';

export type AppMode = 'auto' | 'mobile' | 'web';

const extra = Constants.expoConfig?.extra ??
  Constants.manifest?.extra ??
  Constants.manifest2?.extra?.expoClient?.extra ??
  {};

const configuredMode = extra.APP_MODE as AppMode | undefined;

export const APP_MODE: AppMode = configuredMode === 'mobile' || configuredMode === 'web'
  ? configuredMode
  : 'auto';

export const IS_WEB_MODE = APP_MODE === 'web' || (APP_MODE === 'auto' && Platform.OS === 'web');

export const SERVER_ENDPOINT = String(
  extra.SERVER_ENDPOINT || 'http://192.168.0.102:8000',
).replace(/\/$/, '');

export const toWebSocketUrl = (endpoint: string): string => {
  if (endpoint.startsWith('ws://') || endpoint.startsWith('wss://')) {
    return endpoint;
  }
  if (endpoint.startsWith('https://')) {
    return endpoint.replace('https://', 'wss://');
  }
  if (endpoint.startsWith('http://')) {
    return endpoint.replace('http://', 'ws://');
  }
  return `ws://${endpoint}`;
};