import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Markdown from 'react-native-markdown-display';
import { ChatMessage as ChatMessageType, AIMessageType } from '@/services/AgentWebSocket';
import { skyBlueTheme } from '@/constants/Colors';
import { Iconify } from 'react-native-iconify';

interface ChatMessageProps {
  message: ChatMessageType;
}

const getMessageTypeColor = (messageType?: AIMessageType): string => {
  switch (messageType) {
    case 'success':
      return skyBlueTheme.success;
    case 'warning':
      return skyBlueTheme.warning;
    case 'error':
      return skyBlueTheme.danger;
    case 'planning':
      return skyBlueTheme.accent;
    case 'execution':
      return skyBlueTheme.execution;
    case 'system':
      return skyBlueTheme.system;
    case 'info':
    default:
      return skyBlueTheme.text;
  }
};

const getMessageTypeBgColor = (messageType?: AIMessageType): string => {
  switch (messageType) {
    case 'success':
      return '#E8F5E9';
    case 'warning':
      return '#FFF3E0';
    case 'error':
      return '#FFEBEE';
    case 'planning':
      return '#E3F2FD';
    case 'execution':
      return '#F3E5F5';
    case 'system':
      return '#ECEFF1';  // Light blue-grey for system messages
    case 'info':
    default:
      return skyBlueTheme.cardBackground;
  }
};

export default function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === 'user';
  const isError = message.type === 'error';

  const formatTime = (timestamp: string) => {
    try {
      const date = new Date(timestamp);
      return date.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        hour12: true
      });
    } catch {
      return '';
    }
  };

  return (
    <View style={[
      styles.container,
      isUser ? styles.userContainer : styles.assistantContainer,
    ]}>
      <View style={[
        styles.bubble,
        isUser ? styles.userBubble : styles.assistantBubble,
        isError && styles.errorBubble,
        !isUser && { backgroundColor: getMessageTypeBgColor(message.messageType) },
      ]}>
        {/* Message type indicator for assistant messages */}
        {!isUser && message.messageType && message.messageType !== 'info' && (
          <View style={[styles.typeIndicator, { backgroundColor: getMessageTypeColor(message.messageType) }]}>
            <Text style={styles.typeIndicatorText}>
              {message.messageType.toUpperCase()}
            </Text>
          </View>
        )}

        {/* Map Context Indicator */}
        {message.content.includes('[Map Context:') && (
          <View style={[
            styles.mapIndicator,
            isUser ? styles.mapIndicatorUser : styles.mapIndicatorAssistant
          ]}>
            <Iconify icon="mdi:map-marker-radius" size={14} color={isUser ? 'rgba(255,255,255,0.9)' : skyBlueTheme.accent} />
            <Text style={[
              styles.mapIndicatorText,
              isUser ? styles.mapIndicatorTextUser : styles.mapIndicatorTextAssistant
            ]}>
              Location Attached
            </Text>
          </View>
        )}

        <View style={styles.markdownContainer}>
          <Markdown
            style={{
              body: {
                ...styles.text,
                ...(isUser ? styles.userText : styles.assistantText),
                ...(isError && styles.errorText),
                flexShrink: 1,
              },
              paragraph: {
                marginTop: 0,
                marginBottom: 8,
                flexShrink: 1,
              },
              text: {
                ...styles.text,
                ...(isUser ? styles.userText : styles.assistantText),
                ...(isError && styles.errorText),
                flexShrink: 1,
              },
              heading1: {
                fontSize: 20,
                fontWeight: '700',
                marginTop: 12,
                marginBottom: 8,
                ...(isUser ? styles.userText : styles.assistantText),
              },
              heading2: {
                fontSize: 18,
                fontWeight: '700',
                marginTop: 10,
                marginBottom: 6,
                ...(isUser ? styles.userText : styles.assistantText),
              },
              heading3: {
                fontSize: 16,
                fontWeight: '700',
                marginTop: 8,
                marginBottom: 4,
                ...(isUser ? styles.userText : styles.assistantText),
              },
              code_inline: {
                backgroundColor: isUser
                  ? 'rgba(255, 255, 255, 0.2)'
                  : 'rgba(0, 0, 0, 0.1)',
                paddingHorizontal: 4,
                paddingVertical: 2,
                borderRadius: 4,
                fontFamily: 'monospace',
                fontSize: 14,
                ...(isUser ? styles.userText : styles.assistantText),
                flexShrink: 1,
              },
              code_block: {
                backgroundColor: isUser
                  ? 'rgba(255, 255, 255, 0.15)'
                  : 'rgba(0, 0, 0, 0.05)',
                padding: 12,
                borderRadius: 8,
                marginVertical: 8,
                borderWidth: 1,
                borderColor: isUser
                  ? 'rgba(255, 255, 255, 0.2)'
                  : 'rgba(0, 0, 0, 0.1)',
                flexShrink: 1,
              },
              fence: {
                backgroundColor: isUser
                  ? 'rgba(255, 255, 255, 0.15)'
                  : 'rgba(0, 0, 0, 0.05)',
                padding: 12,
                borderRadius: 8,
                marginVertical: 8,
                borderWidth: 1,
                borderColor: isUser
                  ? 'rgba(255, 255, 255, 0.2)'
                  : 'rgba(0, 0, 0, 0.1)',
                flexShrink: 1,
              },
              link: {
                color: isUser ? '#FFFFFF' : skyBlueTheme.accent,
                textDecorationLine: 'underline',
              },
              strong: {
                fontWeight: '700',
              },
              em: {
                fontStyle: 'italic',
              },
              blockquote: {
                borderLeftWidth: 4,
                borderLeftColor: isUser ? 'rgba(255, 255, 255, 0.5)' : skyBlueTheme.accent,
                paddingLeft: 12,
                marginVertical: 8,
                backgroundColor: isUser
                  ? 'rgba(255, 255, 255, 0.1)'
                  : 'rgba(0, 0, 0, 0.03)',
                paddingVertical: 8,
                paddingRight: 12,
                borderRadius: 4,
              },
              bullet_list: {
                marginVertical: 4,
                flexShrink: 1,
              },
              ordered_list: {
                marginVertical: 4,
                flexShrink: 1,
              },
              list_item: {
                marginVertical: 4,
                flexShrink: 1,
                flexWrap: 'wrap',
                paddingLeft: 4,
              },
              bullet_list_icon: {
                marginLeft: 0,
                marginRight: 8,
                color: isUser ? '#FFFFFF' : '#000000',
              },
              ordered_list_icon: {
                marginLeft: 0,
                marginRight: 8,
                color: isUser ? '#FFFFFF' : '#000000',
              },
              list_item_content: {
                flexShrink: 1,
                flexWrap: 'wrap',
              },
              hr: {
                backgroundColor: isUser
                  ? 'rgba(255, 255, 255, 0.3)'
                  : 'rgba(0, 0, 0, 0.2)',
                height: 1,
                marginVertical: 12,
              },
              table: {
                borderWidth: 1,
                borderColor: isUser
                  ? 'rgba(255, 255, 255, 0.3)'
                  : 'rgba(0, 0, 0, 0.2)',
                borderRadius: 8,
                marginVertical: 8,
                overflow: 'hidden',
              },
              thead: {
                backgroundColor: isUser
                  ? 'rgba(255, 255, 255, 0.2)'
                  : 'rgba(0, 0, 0, 0.1)',
              },
              th: {
                padding: 8,
                fontWeight: '700',
                borderRightWidth: 1,
                borderRightColor: isUser
                  ? 'rgba(255, 255, 255, 0.3)'
                  : 'rgba(0, 0, 0, 0.2)',
              },
              td: {
                padding: 8,
                borderRightWidth: 1,
                borderTopWidth: 1,
                borderRightColor: isUser
                  ? 'rgba(255, 255, 255, 0.3)'
                  : 'rgba(0, 0, 0, 0.2)',
                borderTopColor: isUser
                  ? 'rgba(255, 255, 255, 0.3)'
                  : 'rgba(0, 0, 0, 0.2)',
              },
            }}
          >
            {/* Add zero-width space if content starts with a list to fix parsing issue */}
            {`\u200B${message.content.replace(/\n\[Map Context: Lat: .*, Lon: .*\]/g, '')}`}

          </Markdown>
        </View>

        <Text style={[
          styles.timestamp,
          isUser ? styles.userTimestamp : styles.assistantTimestamp,
        ]}>
          {formatTime(message.timestamp)}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginVertical: 4,
    marginHorizontal: 12,
  },
  userContainer: {
    alignItems: 'flex-end',
  },
  assistantContainer: {
    alignItems: 'flex-start',
  },
  bubble: {
    maxWidth: '85%',
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 16,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.1,
    shadowRadius: 2,
    elevation: 1,
    flexShrink: 1,
  },
  userBubble: {
    backgroundColor: skyBlueTheme.accent,
    borderBottomRightRadius: 4,
  },
  assistantBubble: {
    backgroundColor: skyBlueTheme.cardBackground,
    borderBottomLeftRadius: 4,
    borderWidth: 0,
  },
  errorBubble: {
    backgroundColor: '#FFEBEE'
  },
  typeIndicator: {
    alignSelf: 'flex-start',
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 10,
    marginBottom: 6,
  },
  typeIndicatorText: {
    fontSize: 10,
    fontWeight: '700',
    color: '#FFFFFF',
    letterSpacing: 0.5,
  },
  markdownContainer: {
    width: '100%',
    flexShrink: 1,
  },
  text: {
    fontSize: 15,
    lineHeight: 20,
  },
  userText: {
    color: '#FFFFFF',
  },
  assistantText: {
    color: skyBlueTheme.text,
  },
  errorText: {
    color: skyBlueTheme.danger,
  },
  timestamp: {
    fontSize: 10,
    marginTop: 1,
    opacity: 0.7,
  },
  userTimestamp: {
    color: 'rgba(255, 255, 255, 0.8)',
    textAlign: 'right',
  },
  assistantTimestamp: {
    color: skyBlueTheme.text,
    textAlign: 'left',
  },
  mapIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginBottom: 6,
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 12,
    alignSelf: 'flex-start',
  },
  mapIndicatorUser: {
    backgroundColor: 'rgba(255, 255, 255, 0.2)',
  },
  mapIndicatorAssistant: {
    backgroundColor: 'rgba(0, 0, 0, 0.05)',
  },
  mapIndicatorText: {
    fontSize: 11,
    fontWeight: '600',
  },
  mapIndicatorTextUser: {
    color: '#FFFFFF',
  },
  mapIndicatorTextAssistant: {
    color: skyBlueTheme.accent,
  },
});
