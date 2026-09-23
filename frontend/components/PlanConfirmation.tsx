import React, { useState, useMemo, memo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  TextInput,
  Modal,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { Iconify } from 'react-native-iconify';
import { PlanData, PlanWaypoint } from '@/services/AgentWebSocket';
import { skyBlueTheme } from '@/constants/Colors';

interface PlanConfirmationProps {
  planData: PlanData;
  visible: boolean;
  onConfirm: () => void;
  onAbort: (feedback?: string) => void;
}

function WaypointItem({ waypoint, index }: { waypoint: PlanWaypoint; index: number }) {
  const getActionIcon = (action: string): { icon: string; color: string } => {
    switch (action) {
      case 'takeoff':
        return { icon: 'mdi:airplane-takeoff', color: skyBlueTheme.success };
      case 'land':
        return { icon: 'mdi:airplane-landing', color: skyBlueTheme.danger };
      case 'hover':
        return { icon: 'mdi:pause-circle', color: skyBlueTheme.accent };
      case 'yaw':
        return { icon: 'mdi:rotate-right', color: skyBlueTheme.accent };
      case 'move_to':
      default:
        return { icon: 'mdi:map-marker-path', color: skyBlueTheme.accent };
    }
  };

  const icon = getActionIcon(waypoint.action);

  return (
    <View style={styles.waypointItem}>
      <View style={styles.waypointNumber}>
        <Text style={styles.waypointNumberText}>{index + 1}</Text>
      </View>
      <View style={styles.waypointContent}>
        <View style={styles.waypointHeader}>
          <Iconify icon={icon.icon} size={14} color={icon.color} />
          <Text style={styles.waypointAction}>{waypoint.action.replace('_', ' ').toUpperCase()}</Text>
        </View>
        <Text style={styles.waypointDescription}>{waypoint.description}</Text>
        {waypoint.position && (
          <Text style={styles.waypointPosition}>
            ({waypoint.position.lat.toFixed(5)}, {waypoint.position.lon.toFixed(5)}, {waypoint.position.alt}m)
          </Text>
        )}
      </View>
    </View>
  );
}

function PlanConfirmation({
  planData,
  visible,
  onConfirm,
  onAbort
}: PlanConfirmationProps) {
  const [showFeedback, setShowFeedback] = useState(false);
  const [feedback, setFeedback] = useState('');

  const handleAbort = () => {
    if (feedback.trim()) {
      onAbort(feedback.trim());
    } else {
      setShowFeedback(true);
    }
  };

  const handleAbortWithFeedback = () => {
    onAbort(feedback.trim() || undefined);
    setShowFeedback(false);
    setFeedback('');
  };

  const formatDuration = (seconds?: number): string => {
    if (!seconds) return 'Unknown';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return `${mins}m ${secs}s`;
  };

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={() => onAbort()}
    >
      <KeyboardAvoidingView
        style={styles.overlay}
        behavior='padding'
        keyboardVerticalOffset={Platform.OS === 'ios' ? 0 : 20}
        enabled={!showFeedback}
      >
        <View style={styles.container}>
          {/* Header */}
          <View style={styles.header}>
            <Text style={styles.headerTitle}>Mission Plan</Text>
            <View style={styles.headerBadge}>
              <Text style={styles.headerBadgeText}>
                {planData.waypoint_count} waypoints
              </Text>
            </View>
          </View>

          {/* Summary */}
          <View style={styles.summary}>
            <Text style={styles.summaryText}>{planData.summary}</Text>
            {!!planData.estimated_duration && (
              <View style={styles.durationContainer}>
                <Text style={styles.durationLabel}>Estimated Duration:</Text>
                <Text style={styles.durationValue}>
                  {formatDuration(planData.estimated_duration)}
                </Text>
              </View>
            )}
          </View>

          {/* Waypoints List */}
          <ScrollView
            style={[styles.waypointsList, showFeedback && styles.waypointsListReduced]}
            showsVerticalScrollIndicator={false}
            nestedScrollEnabled={true}
          >
            {useMemo(
              () =>
                planData.waypoints.map((waypoint, index) => (
                  <WaypointItem key={waypoint.id} waypoint={waypoint} index={index} />
                )),
              [planData.waypoints]
            )}
          </ScrollView>

          {/* Feedback Input (shown when aborting) */}
          {showFeedback && (
            <View style={styles.feedbackContainer}>
              <Text style={styles.feedbackLabel}>
                Any feedback for improving the plan?
              </Text>
              <TextInput
                style={styles.feedbackInput}
                placeholder="e.g., Hover for 5 seconds instead..."
                value={feedback}
                onChangeText={setFeedback}
                multiline
                placeholderTextColor="#999"
              />
              <View style={styles.feedbackButtons}>
                <TouchableOpacity
                  style={[styles.feedbackButton, styles.cancelFeedbackButton]}
                  onPress={() => {
                    setShowFeedback(false);
                    setFeedback('');
                  }}
                >
                  <Text style={styles.cancelFeedbackText}>Back</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.feedbackButton, styles.submitFeedbackButton]}
                  onPress={handleAbortWithFeedback}
                >
                  <Text style={styles.submitFeedbackText}>
                    {feedback.trim() ? 'Submit Feedback' : 'Cancel Plan'}
                  </Text>
                </TouchableOpacity>
              </View>
            </View>
          )}

          {/* Action Buttons */}
          {!showFeedback && (
            <View style={styles.buttonContainer}>
              <TouchableOpacity
                style={[styles.button, styles.abortButton]}
                onPress={handleAbort}
              >
                <Text style={styles.abortButtonText}>Abort</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.button, styles.confirmButton]}
                onPress={onConfirm}
              >
                <Text style={styles.confirmButtonText}>Confirm & Execute</Text>
              </TouchableOpacity>
            </View>
          )}
        </View>
      </KeyboardAvoidingView>
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
    borderRadius: 20,
    width: '100%',
    maxHeight: '85%',
    minHeight: 300,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 8,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingTop: 20,
    paddingBottom: 12,
    borderBottomWidth: 1,
    borderBottomColor: skyBlueTheme.border,
  },
  headerTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: skyBlueTheme.text,
  },
  headerBadge: {
    backgroundColor: skyBlueTheme.accent,
    paddingHorizontal: 12,
    paddingVertical: 4,
    borderRadius: 12,
  },
  headerBadgeText: {
    fontSize: 12,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  summary: {
    padding: 16,
    backgroundColor: '#F8F9FA',
    marginHorizontal: 16,
    marginTop: 12,
    borderRadius: 12,
  },
  summaryText: {
    fontSize: 15,
    color: skyBlueTheme.text,
    lineHeight: 22,
  },
  durationContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 8,
  },
  durationLabel: {
    fontSize: 13,
    color: '#666',
  },
  durationValue: {
    fontSize: 13,
    fontWeight: '600',
    color: skyBlueTheme.accent,
    marginLeft: 4,
  },
  waypointsList: {
    maxHeight: 250,
    marginHorizontal: 16,
    marginTop: 12,
  },
  waypointsListReduced: {
    maxHeight: 150,
  },
  waypointItem: {
    flexDirection: 'row',
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: skyBlueTheme.border,
  },
  waypointNumber: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: skyBlueTheme.accent,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 12,
  },
  waypointNumberText: {
    fontSize: 14,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  waypointContent: {
    flex: 1,
  },
  waypointHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  waypointAction: {
    fontSize: 12,
    fontWeight: '600',
    color: skyBlueTheme.darkSkyBlue,
  },
  waypointDescription: {
    fontSize: 14,
    color: skyBlueTheme.text,
    marginTop: 4,
  },
  waypointPosition: {
    fontSize: 11,
    color: '#888',
    marginTop: 2,
    fontFamily: 'monospace',
  },
  feedbackContainer: {
    padding: 16,
    paddingBottom: 40,
    borderTopWidth: 1,
    borderTopColor: skyBlueTheme.border,
  },
  feedbackLabel: {
    fontSize: 14,
    fontWeight: '600',
    color: skyBlueTheme.text,
    marginBottom: 8,
  },
  feedbackInput: {
    borderWidth: 1,
    borderColor: skyBlueTheme.border,
    borderRadius: 12,
    padding: 12,
    fontSize: 14,
    minHeight: 80,
    textAlignVertical: 'top',
    backgroundColor: '#F8F9FA',
    color: skyBlueTheme.text,
  },
  feedbackButtons: {
    flexDirection: 'row',
    gap: 12,
    marginTop: 12,
    marginBottom: 8,
  },
  feedbackButton: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 12,
    alignItems: 'center',
  },
  cancelFeedbackButton: {
    backgroundColor: '#E0E0E0',
  },
  cancelFeedbackText: {
    fontSize: 14,
    fontWeight: '600',
    color: skyBlueTheme.text,
  },
  submitFeedbackButton: {
    backgroundColor: skyBlueTheme.danger,
  },
  submitFeedbackText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  buttonContainer: {
    flexDirection: 'row',
    padding: 16,
    gap: 12,
    borderTopWidth: 1,
    borderTopColor: skyBlueTheme.border,
  },
  button: {
    flex: 1,
    paddingVertical: 14,
    borderRadius: 12,
    alignItems: 'center',
  },
  abortButton: {
    backgroundColor: '#F5F5F5',
    borderWidth: 2,
    borderColor: skyBlueTheme.danger,
  },
  abortButtonText: {
    fontSize: 16,
    fontWeight: '700',
    color: skyBlueTheme.danger,
  },
  confirmButton: {
    backgroundColor: skyBlueTheme.success,
  },
  confirmButtonText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#FFFFFF',
  },
});

// Memoize component to prevent unnecessary re-renders
// Only re-render if planData.plan_id changes (new plan) or visible/onConfirm/onAbort change
export default memo(PlanConfirmation, (prevProps, nextProps) => {
  return (
    prevProps.visible === nextProps.visible &&
    prevProps.planData?.plan_id === nextProps.planData?.plan_id &&
    prevProps.onConfirm === nextProps.onConfirm &&
    prevProps.onAbort === nextProps.onAbort
  );
});
