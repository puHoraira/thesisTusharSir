import React from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Modal,
} from 'react-native';
import { skyBlueTheme } from '@/constants/Colors';

export interface DialogButton {
  text: string;
  onPress: () => void;
  style?: 'default' | 'destructive' | 'cancel';
}

export interface DialogProps {
  visible: boolean;
  title: string;
  message: string;
  buttons: DialogButton[];
  onClose?: () => void;
}

export default function Dialog({
  visible,
  title,
  message,
  buttons,
  onClose,
}: DialogProps) {
  const getButtonStyle = (style?: string) => {
    switch (style) {
      case 'destructive':
        return styles.destructiveButton;
      case 'cancel':
        return styles.cancelButton;
      default:
        return styles.defaultButton;
    }
  };

  const getButtonTextStyle = (style?: string) => {
    switch (style) {
      case 'destructive':
        return styles.destructiveButtonText;
      case 'cancel':
        return styles.cancelButtonText;
      default:
        return styles.defaultButtonText;
    }
  };

  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={onClose}
    >
      <View style={styles.overlay}>
        <View style={styles.container}>
          <View style={styles.header}>
            <Text style={styles.title}>{title}</Text>
          </View>

          <View style={styles.content}>
            <Text style={styles.message}>{message}</Text>
          </View>

          <View style={styles.buttonContainer}>
            {buttons.map((button, index) => (
              <TouchableOpacity
                key={index}
                style={[
                  styles.button,
                  getButtonStyle(button.style),
                  buttons.length > 2 && styles.buttonMultiple,
                ]}
                onPress={() => {
                  button.onPress();
                  if (onClose) {
                    onClose();
                  }
                }}
              >
                <Text style={getButtonTextStyle(button.style)}>
                  {button.text}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
  },
  container: {
    backgroundColor: skyBlueTheme.cardBackground,
    borderRadius: 16,
    width: '100%',
    maxWidth: 400,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 8,
  },
  header: {
    paddingHorizontal: 20,
    paddingTop: 20,
    paddingBottom: 12,
  },
  title: {
    fontSize: 20,
    fontWeight: '700',
    color: skyBlueTheme.text,
  },
  content: {
    paddingHorizontal: 20,
    paddingBottom: 20,
  },
  message: {
    fontSize: 15,
    color: '#666',
    lineHeight: 22,
  },
  buttonContainer: {
    flexDirection: 'row',
    borderTopWidth: 1,
    borderTopColor: skyBlueTheme.border,
    paddingHorizontal: 12,
    paddingVertical: 8,
    gap: 8,
  },
  button: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  buttonMultiple: {
    flex: 0,
    paddingHorizontal: 16,
  },
  defaultButton: {
    backgroundColor: skyBlueTheme.accent,
  },
  defaultButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  destructiveButton: {
    backgroundColor: skyBlueTheme.danger,
  },
  destructiveButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  cancelButton: {
    backgroundColor: '#F5F5F5',
    borderWidth: 1,
    borderColor: skyBlueTheme.border,
  },
  cancelButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: skyBlueTheme.text,
  },
});
