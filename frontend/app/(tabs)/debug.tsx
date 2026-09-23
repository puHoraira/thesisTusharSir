import React, { useEffect, useState } from 'react';
import {
  StyleSheet,
  View,
  Text,
  ScrollView,
  TouchableOpacity,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { skyBlueTheme } from '@/constants/Colors';

// Intercept console.log
const originalLog = console.log;
const originalWarn = console.warn;
const originalError = console.error;

let logs: string[] = [];

console.log = (...args) => {
  const message = args.map(arg => 
    typeof arg === 'object' ? JSON.stringify(arg) : String(arg)
  ).join(' ');
  logs.push(`[LOG] ${message}`);
  if (logs.length > 100) logs.shift(); // Keep last 100
  originalLog(...args);
};

console.warn = (...args) => {
  const message = args.map(arg => 
    typeof arg === 'object' ? JSON.stringify(arg) : String(arg)
  ).join(' ');
  logs.push(`[WARN] ${message}`);
  if (logs.length > 100) logs.shift();
  originalWarn(...args);
};

console.error = (...args) => {
  const message = args.map(arg => 
    typeof arg === 'object' ? JSON.stringify(arg) : String(arg)
  ).join(' ');
  logs.push(`[ERROR] ${message}`);
  if (logs.length > 100) logs.shift();
  originalError(...args);
};

export default function DebugScreen() {
  const [displayLogs, setDisplayLogs] = useState<string[]>([]);
  
  useEffect(() => {
    // Update logs every 500ms
    const interval = setInterval(() => {
      setDisplayLogs([...logs]);
    }, 500);
    
    return () => clearInterval(interval);
  }, []);
  
  const clearLogs = () => {
    logs = [];
    setDisplayLogs([]);
  };
  
  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Text style={styles.title}>Debug Console</Text>
        <TouchableOpacity onPress={clearLogs} style={styles.clearButton}>
          <Text style={styles.clearButtonText}>Clear</Text>
        </TouchableOpacity>
      </View>
      
      <ScrollView 
        style={styles.logContainer}
        contentContainerStyle={styles.logContent}
      >
        {displayLogs.length === 0 ? (
          <Text style={styles.emptyText}>No logs yet...</Text>
        ) : (
          displayLogs.map((log, index) => (
            <Text 
              key={index} 
              style={[
                styles.logText,
                log.includes('[ERROR]') && styles.errorText,
                log.includes('[WARN]') && styles.warnText,
                (log.includes('[TX]') || log.includes('[RX]') || log.includes('[CONNECT]')) && styles.highlightText,
              ]}
            >
              {log}
            </Text>
          ))
        )}
      </ScrollView>
      
      <View style={styles.footer}>
        <Text style={styles.footerText}>
          Look for [CONNECT], [TX], and [RX] messages
        </Text>
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
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 16,
    backgroundColor: skyBlueTheme.cardBackground,
    borderBottomWidth: 1,
    borderBottomColor: skyBlueTheme.border,
  },
  title: {
    fontSize: 20,
    fontWeight: '700',
    color: skyBlueTheme.text,
  },
  clearButton: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    backgroundColor: skyBlueTheme.danger,
    borderRadius: 8,
  },
  clearButtonText: {
    color: '#FFFFFF',
    fontWeight: '600',
  },
  logContainer: {
    flex: 1,
  },
  logContent: {
    padding: 12,
  },
  logText: {
    fontSize: 11,
    fontFamily: 'monospace',
    color: skyBlueTheme.text,
    marginBottom: 4,
  },
  errorText: {
    color: skyBlueTheme.danger,
  },
  warnText: {
    color: skyBlueTheme.warning,
  },
  highlightText: {
    color: skyBlueTheme.accent,
    fontWeight: 'bold',
  },
  emptyText: {
    fontSize: 14,
    color: skyBlueTheme.textSecondary,
    textAlign: 'center',
    marginTop: 40,
  },
  footer: {
    padding: 12,
    backgroundColor: skyBlueTheme.cardBackground,
    borderTopWidth: 1,
    borderTopColor: skyBlueTheme.border,
  },
  footerText: {
    fontSize: 12,
    color: skyBlueTheme.textSecondary,
    textAlign: 'center',
  },
});
