import React, { useEffect, useState, useRef, useCallback } from 'react';
import {
  StyleSheet,
  View,
  Text,
  TextInput,
  TouchableOpacity,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  ActivityIndicator,
  Keyboard,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Iconify } from 'react-native-iconify';
import ChatMessageComponent from '@/components/ChatMessage';
import PlanConfirmation from '@/components/PlanConfirmation';
import Dialog, { DialogButton } from '@/components/Dialog';
import CompactTelemetryPanel from '@/components/CompactTelemetryPanel';
import { agentWebSocket } from '@/services/AgentWebSocket';
import { useMapSelection } from '@/contexts/MapSelectionContext';
import { useDroneTrail } from '@/contexts/DroneTrailContext';
import { skyBlueTheme } from '@/constants/Colors';

interface TelemetryState {
  velocity?: number;
  altitude?: number;
  flight_mode?: string;
  battery?: number;
}

interface ChatMessage {
  id: string;
  type: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  messageType?: string;
}

interface PlanData {
  plan_id: string;
  description: string;
  steps: any[];
}

export default function AIChatScreen() {
  const [connected, setConnected] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputText, setInputText] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [pendingPlan, setPendingPlan] = useState<PlanData | null>(null);
  const [keyboardHeight, setKeyboardHeight] = useState(0);
  const [telemetry, setTelemetry] = useState<TelemetryState>({});
  const { selectedLocation, isMapModeActive, setMapModeActive } = useMapSelection();
  const { startNewSegment, clearAllTrails, activeSegment } = useDroneTrail();

  const [dialog, setDialog] = useState<{
    visible: boolean;
    title: string;
    message: string;
    buttons: DialogButton[];
  }>({
    visible: false,
    title: '',
    message: '',
    buttons: [],
  });

  const flatListRef = useRef<FlatList>(null);

  // Connect to WebSocket and subscribe to events
  useEffect(() => {
    agentWebSocket.connect();

    // Subscribe to connection status
    const unsubscribeConnection = agentWebSocket.onConnectionChange((isConnected) => {
      setConnected(isConnected);
      if (!isConnected) {
        setIsLoading(false);
      }
    });

    // Subscribe to AI messages
    const unsubscribeMessage = agentWebSocket.onMessage((message) => {
      setIsLoading(false);

      // End current segment and start a new one when mission completes
      if (message.messageType === 'success') {
        startNewSegment();
      }

      setMessages((prev) => [...prev, message]);
    });

    // Subscribe to plan confirmations
    const unsubscribePlan = agentWebSocket.onPlanConfirmation((planData) => {
      setPendingPlan((prev) => {
        if (prev?.plan_id === planData.plan_id) {
          return prev;
        }
        return planData;
      });
    });

    // Telemetry updates not available in AgentWebSocket
    // (Use DroneWebSocket from control tab if needed)

    // Cleanup on unmount
    return () => {
      unsubscribeConnection();
      unsubscribeMessage();
      unsubscribePlan();
      agentWebSocket.disconnect();
    };
  }, [startNewSegment]);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (messages.length > 0) {
      setTimeout(() => {
        flatListRef.current?.scrollToEnd({ animated: true });
      }, 100);
    }
  }, [messages]);

  // Handle keyboard events for Android
  useEffect(() => {
    if (Platform.OS === 'android') {
      const keyboardWillShowListener = Keyboard.addListener(
        'keyboardDidShow',
        (e) => {
          setKeyboardHeight(e.endCoordinates.height);
          // Scroll to bottom when keyboard appears
          setTimeout(() => {
            flatListRef.current?.scrollToEnd({ animated: true });
          }, 100);
        }
      );
      const keyboardWillHideListener = Keyboard.addListener(
        'keyboardDidHide',
        () => {
          setKeyboardHeight(0);
        }
      );

      return () => {
        keyboardWillShowListener.remove();
        keyboardWillHideListener.remove();
      };
    }
  }, []);

  // Use effect to auto-disable map mode if selection is cleared
  useEffect(() => {
    if (!selectedLocation && isMapModeActive) {
      setMapModeActive(false);
    }
  }, [selectedLocation, isMapModeActive]);

  // Toggle Map Mode
  const toggleMapMode = () => {
    if (selectedLocation) {
      setMapModeActive(!isMapModeActive);
    }
  };

  // Send message handler
  const handleSend = useCallback(() => {
    const text = inputText.trim();
    if (!text || !connected) return;

    // Prepare message content (appending map context if active)
    let messageContent = text;
    let displayContent = text; // Content shown in chat UI (might differ effectively if we want to hide context, but simply showing it is safer for now)

    if (isMapModeActive && selectedLocation) {
      const mapContextParams = `\n[Map Context: Lat: ${selectedLocation.latitude.toFixed(6)}, Lon: ${selectedLocation.longitude.toFixed(6)}]`;
      messageContent += mapContextParams;
      // We will show the same content to the user so they know what was sent
      // Alternatively, we could hide it, but transparency is often better.
    }

    // Send to server via serial
    const userMessage: ChatMessage = {
      id: `user_${Date.now()}`,
      type: 'ai_message',
      role: 'user',
      content: messageContent,
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMessage]);

    // Send agent command
    agentWebSocket.sendCommand(messageContent);
    setInputText('');
    setMapModeActive(false);
    setIsLoading(true);

    // Start a trail segment if none exists
    if (!activeSegment) {
      startNewSegment();
    }
  }, [inputText, connected, isMapModeActive, selectedLocation, activeSegment, startNewSegment]);

  // Confirm plan handler
  const handleConfirmPlan = useCallback(() => {
    if (!pendingPlan) return;

    agentWebSocket.confirmPlan(pendingPlan.plan_id);
    setPendingPlan(null);
  }, [pendingPlan]);

  // Abort plan handler
  const handleAbortPlan = useCallback((feedback?: string) => {
    if (!pendingPlan) return;

    agentWebSocket.abortPlan(pendingPlan.plan_id, feedback);
    setPendingPlan(null);
  }, [pendingPlan]);

  // Emergency stop handler
  const handleEmergencyStop = useCallback(() => {
    setDialog({
      visible: true,
      title: 'Emergency Stop',
      message: 'This will immediately stop all drone operations. Are you sure?',
      buttons: [
        {
          text: 'Cancel',
          onPress: () => { },
          style: 'cancel',
        },
        {
          text: 'STOP',
          onPress: () => {
            agentWebSocket.emergencyStop();
          },
          style: 'destructive',
        },
      ],
    });
  }, []);

  // Clear chat handler
  const handleClearChat = useCallback(() => {
    if (messages.length === 0) return;

    setDialog({
      visible: true,
      title: 'Clear Chat',
      message: 'This will delete all messages and start a new conversation. Continue?',
      buttons: [
        {
          text: 'Cancel',
          onPress: () => { },
          style: 'cancel',
        },
        {
          text: 'Clear',
          onPress: () => {
            setMessages([]);
            setPendingPlan(null);
            setIsLoading(false);
            agentWebSocket.clearHistory();
            clearAllTrails();
          },
          style: 'destructive',
        },
      ],
    });
  }, [messages.length, clearAllTrails]);

  // Render message item
  const renderMessage = useCallback(({ item }: { item: ChatMessage }) => {
    return <ChatMessageComponent message={item} />;
  }, []);

  // Empty state
  const renderEmptyState = () => (
    <View style={styles.emptyState}>
      <Iconify icon="eos-icons:drone" size={48} color={skyBlueTheme.border} />
      <Text style={styles.emptyTitle}>AI Drone Agent</Text>
      <Text style={styles.emptyText}>
        Send a message to control your drone with natural language.
        Try commands like:
      </Text>
      <View style={styles.exampleContainer}>
        <Text style={styles.exampleText}>"Take off and fly 10 meters forward"</Text>
        <Text style={styles.exampleText}>"Fly to 50m north, hover for 5 seconds, then return"</Text>
        <Text style={styles.exampleText}>"What's the current battery level?"</Text>
      </View>
    </View>
  );

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <View style={styles.headerContent}>
          <View style={styles.titleContainer}>
            <Iconify icon="ix:ai" size={20} color={skyBlueTheme.accent} />
            <Text style={styles.title}>AI Agent</Text>
            {/* Connection Status Indicator */}
            <View
              style={[
                styles.statusIndicator,
                connected
                  ? { backgroundColor: skyBlueTheme.success }
                  : { backgroundColor: skyBlueTheme.danger },
              ]}
            />
          </View>
          <View style={styles.headerActions}>
            {/* Clear Chat Button */}
            <TouchableOpacity
              style={[
                styles.clearButton,
                messages.length === 0 && styles.clearButtonDisabled,
              ]}
              onPress={handleClearChat}
              disabled={messages.length === 0}
            >
              <Iconify
                icon="mdi:refresh"
                size={25}
                color={messages.length === 0 ? '#CCC' : skyBlueTheme.accent}
              />
            </TouchableOpacity>
          </View>
        </View>

        {/* Telemetry Panel */}
        <View style={{ marginTop: 8 }}>
          <CompactTelemetryPanel telemetry={telemetry} />
        </View>

        {/* Emergency Stop Button */}
        <TouchableOpacity
          style={styles.emergencyButton}
          onPress={handleEmergencyStop}
        >
          <Iconify icon="mdi:stop-circle" size={16} color="#FFFFFF" />
          <Text style={styles.emergencyButtonText}>STOP</Text>
        </TouchableOpacity>
      </View>

      {/* Chat Messages */}
      <KeyboardAvoidingView
        style={styles.chatContainer}
        behavior={Platform.OS === 'ios' ? 'padding' : 'padding'}
        keyboardVerticalOffset={Platform.OS === 'ios' ? 90 : 0}
        enabled={Platform.OS === 'ios'}
      >
        <FlatList
          ref={flatListRef}
          data={messages}
          renderItem={renderMessage}
          keyExtractor={(item) => item.id}
          contentContainerStyle={[
            styles.messagesList,
            messages.length === 0 && styles.emptyList,
          ]}
          ListEmptyComponent={renderEmptyState}
          showsVerticalScrollIndicator={false}
        />

        {/* Loading Indicator */}
        {isLoading && (
          <View style={styles.loadingContainer}>
            <ActivityIndicator size="small" color={skyBlueTheme.accent} />
            <Text style={styles.loadingText}>AI is thinking...</Text>
          </View>
        )}

        {/* Input Area */}
        <View
          style={[
            styles.inputContainer,
            Platform.OS === 'android' && {
              paddingBottom: keyboardHeight > 0 ? keyboardHeight : 10,
            },
          ]}
        >
          {/* Map Tool Button */}
          <TouchableOpacity
            style={[
              styles.toolButton,
              !selectedLocation && styles.toolButtonDisabled,
              isMapModeActive && styles.toolButtonActive,
            ]}
            onPress={toggleMapMode}
            disabled={!selectedLocation}
          >
            <Iconify
              icon="mdi:map-marker-radius"
              size={22}
              color={
                !selectedLocation ? '#CCC' :
                  isMapModeActive ? '#FFFFFF' : skyBlueTheme.text
              }
            />
          </TouchableOpacity>

          <TextInput
            style={styles.input}
            placeholder={connected ? "Type a command..." : "Connecting..."}
            placeholderTextColor="#999"
            value={inputText}
            onChangeText={setInputText}
            onSubmitEditing={handleSend}
            returnKeyType="send"
            editable={connected}
            multiline
            maxLength={500}
          />
          <TouchableOpacity
            style={[
              styles.sendButton,
              (!connected || !inputText.trim() || isLoading) && styles.sendButtonDisabled,
            ]}
            onPress={handleSend}
            disabled={!connected || !inputText.trim() || isLoading}
          >
            <Iconify
              icon="fa:paper-plane"
              size={18}
              color={(!connected || !inputText.trim() || isLoading) ? '#CCC' : '#FFFFFF'}
            />
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>

      {/* Plan Confirmation Modal */}
      {pendingPlan && (
        <PlanConfirmation
          planData={pendingPlan}
          visible={!!pendingPlan}
          onConfirm={handleConfirmPlan}
          onAbort={handleAbortPlan}
        />
      )}

      {/* Dialog Modal */}
      <Dialog
        visible={dialog.visible}
        title={dialog.title}
        message={dialog.message}
        buttons={dialog.buttons}
        onClose={() => setDialog({ ...dialog, visible: false })}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: skyBlueTheme.background,
    borderTopWidth: 0,
  },
  header: {
    backgroundColor: skyBlueTheme.cardBackground,
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderTopWidth: 0,
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
  headerActions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  clearButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: skyBlueTheme.background,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: skyBlueTheme.border,
  },
  clearButtonDisabled: {
    opacity: 0.5,
  },
  statusContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  statusIndicator: {
    width: 10,
    height: 10,
    borderRadius: 5,
    marginLeft: 4,
  },
  statusText: {
    fontSize: 12,
    fontWeight: '500',
    color: skyBlueTheme.text,
  },
  // Telemetry styles removed in favor of CompactTelemetryPanel
  emergencyButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: skyBlueTheme.danger,
    paddingVertical: 8,
    paddingHorizontal: 16,
    borderRadius: 8,
    marginTop: 10,
    gap: 6,
  },
  emergencyButtonText: {
    fontSize: 12,
    fontWeight: '700',
    color: '#FFFFFF',
    letterSpacing: 0.5,
  },
  chatContainer: {
    flex: 1,
  },
  messagesList: {
    paddingVertical: 12,
  },
  emptyList: {
    flex: 1,
    justifyContent: 'center',
  },
  emptyState: {
    alignItems: 'center',
    paddingHorizontal: 32,
  },
  emptyTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: skyBlueTheme.text,
    marginTop: 16,
    marginBottom: 8,
  },
  emptyText: {
    fontSize: 14,
    color: '#666',
    textAlign: 'center',
    lineHeight: 20,
  },
  exampleContainer: {
    marginTop: 16,
    alignItems: 'center',
  },
  exampleText: {
    fontSize: 13,
    color: skyBlueTheme.accent,
    fontStyle: 'italic',
    marginVertical: 4,
    textAlign: 'center',
  },
  loadingContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 12,
    gap: 8,
  },
  loadingText: {
    fontSize: 13,
    color: '#666',
  },
  inputContainer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    paddingHorizontal: 12,
    paddingVertical: 10,
    backgroundColor: skyBlueTheme.cardBackground,
    borderTopWidth: 0,
    borderTopColor: skyBlueTheme.cardBackground,
    gap: 10,
  },
  toolButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#F5F5F5',
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#E0E0E0',
  },
  toolButtonDisabled: {
    opacity: 0.5,
  },
  toolButtonActive: {
    backgroundColor: skyBlueTheme.accent,
    borderColor: skyBlueTheme.accent,
  },
  input: {
    flex: 1,
    backgroundColor: '#F5F5F5',
    borderRadius: 20,
    paddingHorizontal: 16,
    paddingVertical: 10,
    fontSize: 15,
    maxHeight: 100,
    color: skyBlueTheme.text,
  },
  sendButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: skyBlueTheme.accent,
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.2,
    shadowRadius: 3,
    elevation: 3,
  },
  sendButtonDisabled: {
    backgroundColor: '#E0E0E0',
    shadowOpacity: 0,
    elevation: 0,
  },
});
