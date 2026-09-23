import React, { useEffect, useRef } from 'react';
import { View, Text, StyleSheet, Dimensions } from 'react-native';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withSpring,
  runOnJS,
} from 'react-native-reanimated';
import { skyBlueTheme } from '@/constants/Colors';

const { width: SCREEN_WIDTH } = Dimensions.get('window');
const JOYSTICK_SIZE = Math.min(SCREEN_WIDTH * 0.45, 200); // Keep same outer circle size
const HANDLE_SIZE = JOYSTICK_SIZE * 0.3; // Inner handle size
const MAX_DISTANCE = (JOYSTICK_SIZE - HANDLE_SIZE) / 2;
const LINE_LENGTH = JOYSTICK_SIZE * 0.65; // Shorter lines to avoid overlapping with edge labels

interface JoystickProps {
  onMove: (x: number, y: number) => void;
  onRelease?: () => void;
  axisLabels?: { 
    top: string;    // Top label
    bottom: string; // Bottom label
    left: string;   // Left label
    right: string;  // Right label
  };
}

/**
 * Joystick component with drag gesture support
 * Returns normalized values from -1 to 1 for both axes
 */
export default function Joystick({ onMove, onRelease, axisLabels }: JoystickProps) {
  const translateX = useSharedValue(0);
  const translateY = useSharedValue(0);
  const isActive = useSharedValue(false);
  const commandInterval = useRef<NodeJS.Timeout | null>(null);

  // Continuous command emission while held
  const startCommandEmission = () => {
    if (commandInterval.current) {
      clearInterval(commandInterval.current);
    }
    // Emit commands at 20Hz (50ms interval)
    commandInterval.current = setInterval(() => {
      const x = translateX.value / MAX_DISTANCE;
      const y = -translateY.value / MAX_DISTANCE; // Invert Y for intuitive control
      onMove(x, y);
    }, 50) as unknown as NodeJS.Timeout;
  };

  const stopCommandEmission = () => {
    if (commandInterval.current) {
      clearInterval(commandInterval.current);
      commandInterval.current = null;
    }
    // Send zero command on release
    onMove(0, 0);
    if (onRelease) {
      onRelease();
    }
  };

  // Pan gesture handler
  const panGesture = Gesture.Pan()
    .onStart(() => {
      isActive.value = true;
      runOnJS(startCommandEmission)();
    })
    .onUpdate((event) => {
      // Calculate distance from center
      const distance = Math.sqrt(event.translationX ** 2 + event.translationY ** 2);
      
      if (distance > MAX_DISTANCE) {
        // Clamp to circle boundary
        const angle = Math.atan2(event.translationY, event.translationX);
        translateX.value = Math.cos(angle) * MAX_DISTANCE;
        translateY.value = Math.sin(angle) * MAX_DISTANCE;
      } else {
        translateX.value = event.translationX;
        translateY.value = event.translationY;
      }
      
      // Emit immediate command
      const x = translateX.value / MAX_DISTANCE;
      const y = -translateY.value / MAX_DISTANCE;
      runOnJS(onMove)(x, y);
    })
    .onEnd(() => {
      isActive.value = false;
      translateX.value = withSpring(0);
      translateY.value = withSpring(0);
      runOnJS(stopCommandEmission)();
    });

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (commandInterval.current) {
        clearInterval(commandInterval.current);
      }
    };
  }, []);

  // Animated styles
  const joystickStyle = useAnimatedStyle(() => {
    return {
      transform: [
        { translateX: translateX.value },
        { translateY: translateY.value },
      ],
    };
  });

  const containerStyle = useAnimatedStyle(() => {
    return {
      opacity: isActive.value ? 0.8 : 1,
    };
  });

  return (
    <View style={styles.container}>
      <GestureDetector gesture={panGesture}>
        <Animated.View style={[styles.joystickContainer, containerStyle]}>
          {/* Outer circle */}
          <View style={styles.outerCircle} />
          
          {/* Center lines */}
          <View style={styles.centerLineVertical} />
          <View style={styles.centerLineHorizontal} />
          
          {/* Draggable handle */}
          <Animated.View style={[styles.handle, joystickStyle]}>
            <View style={styles.handleInner} />
          </Animated.View>
          
          {/* Axis labels - positioned at the ends of the center lines */}
          {axisLabels && (
            <>
              {/* Top label - at end of vertical line */}
              <View style={styles.labelTop}>
                <Text style={styles.labelTextInside}>{axisLabels.top}</Text>
              </View>
              {/* Bottom label - at end of vertical line */}
              <View style={styles.labelBottom}>
                <Text style={styles.labelTextInside}>{axisLabels.bottom}</Text>
              </View>
              {/* Left label - at edge of circle */}
              <View style={styles.labelLeft}>
                <Text style={styles.labelTextInside}>{axisLabels.left}</Text>
              </View>
              {/* Right label - at edge of circle */}
              <View style={styles.labelRight}>
                <Text style={styles.labelTextInside}>{axisLabels.right}</Text>
              </View>
            </>
          )}
        </Animated.View>
      </GestureDetector>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    width: JOYSTICK_SIZE,
    height: JOYSTICK_SIZE,
    justifyContent: 'center',
    alignItems: 'center',
    marginVertical: 4,
  },
  joystickContainer: {
    width: JOYSTICK_SIZE,
    height: JOYSTICK_SIZE,
    justifyContent: 'center',
    alignItems: 'center',
    position: 'relative',
  },
  outerCircle: {
    width: JOYSTICK_SIZE,
    height: JOYSTICK_SIZE,
    borderRadius: JOYSTICK_SIZE / 2,
    backgroundColor: skyBlueTheme.skyBlue,
    borderWidth: 3,
    borderColor: skyBlueTheme.darkSkyBlue,
    position: 'absolute',
  },
  centerLineVertical: {
    width: 2,
    height: LINE_LENGTH, // Lines extend to edge
    backgroundColor: skyBlueTheme.darkSkyBlue,
    opacity: 0.3,
    position: 'absolute',
  },
  centerLineHorizontal: {
    width: LINE_LENGTH, // Lines extend to edge
    height: 2,
    backgroundColor: skyBlueTheme.darkSkyBlue,
    opacity: 0.3,
    position: 'absolute',
  },
  handle: {
    width: HANDLE_SIZE,
    height: HANDLE_SIZE,
    borderRadius: HANDLE_SIZE / 2,
    backgroundColor: '#FFFFFF',
    borderWidth: 2,
    borderColor: skyBlueTheme.accent,
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.25,
    shadowRadius: 3.84,
    elevation: 5,
  },
  handleInner: {
    width: HANDLE_SIZE * 0.5,
    height: HANDLE_SIZE * 0.5,
    borderRadius: HANDLE_SIZE * 0.25,
    backgroundColor: skyBlueTheme.accent,
  },
  labelTop: {
    position: 'absolute',
    top: JOYSTICK_SIZE * 0.08, // Padding from top edge
    left: '50%',
    transform: [{ translateX: -20 }],
    zIndex: 2,
    width: 40,
    alignItems: 'center',
    justifyContent: 'center',
  },
  labelBottom: {
    position: 'absolute',
    bottom: JOYSTICK_SIZE * 0.08, // Padding from bottom edge
    left: '50%',
    transform: [{ translateX: -20 }],
    zIndex: 2,
    width: 40,
    alignItems: 'center',
    justifyContent: 'center',
  },
  labelLeft: {
    position: 'absolute',
    left: JOYSTICK_SIZE * 0.00, // Aligned with end of horizontal line (line is 65% centered, so ends at 17.5% from edge)
    top: '50%',
    transform: [{ translateY: -7 }], // Centered vertically
    zIndex: 2,
    width: 40,
    alignItems: 'center', // Center aligned for YL/YR and L/R
    justifyContent: 'center',
  },
  labelRight: {
    position: 'absolute',
    right: JOYSTICK_SIZE * 0.00, // Aligned with end of horizontal line (line is 65% centered, so ends at 17.5% from edge)
    top: '50%',
    transform: [{ translateY: -7 }], // Centered vertically
    zIndex: 2,
    width: 40,
    alignItems: 'center', // Center aligned for YL/YR and L/R
    justifyContent: 'center',
  },
  labelTextInside: {
    fontSize: 9,
    fontWeight: '700',
    color: skyBlueTheme.darkSkyBlue,
    textShadowColor: 'rgba(255, 255, 255, 0.9)',
    textShadowOffset: { width: 0, height: 0.5 },
    textShadowRadius: 2,
  },
  labelTextCenter: {
    textAlign: 'center',
  },
});

