import React, { useEffect, useRef } from "react";
import { Animated, Easing, View } from "react-native";

import { useColors } from "@/hooks/useColors";

interface Props {
  /** Outer diameter in px. */
  size?: number;
  /** The moving arc. Defaults to the brand colour. */
  color?: string;
  /** The ring it travels around. Defaults to the hairline colour. */
  track?: string;
  /** Read aloud by a screen reader while this is on screen. */
  label?: string;
}

/**
 * The loading indicator.
 *
 * Replaces react-native's ActivityIndicator, which was used in nine places and
 * is drawn by the operating system: on Android it is Material's spinner in
 * Material's weight, so the one moment the app is asking the officer to wait
 * is the one moment it stopped looking like LookOut. `color` could be set on
 * it, but not the shape.
 *
 * A ring with one coloured quadrant, rotating. The rotation runs on the native
 * driver so it keeps moving smoothly while JavaScript is busy doing the thing
 * being waited for — which is the entire point of a spinner, and something a
 * JS-driven animation gets exactly backwards.
 */
export function Spinner({ size = 24, color, track, label = "Loading" }: Props) {
  const c = useColors();
  const turn = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    const loop = Animated.loop(
      Animated.timing(turn, {
        toValue: 1,
        duration: 760,
        easing: Easing.linear,
        useNativeDriver: true,
      }),
    );
    loop.start();
    return () => loop.stop();
  }, [turn]);

  // Border width scales with the ring: a fixed 3px looks heavy at 16 and thin
  // at 40, and this is drawn at both.
  const stroke = Math.max(2, Math.round(size / 8));

  return (
    <View accessibilityRole="progressbar" accessibilityLabel={label}>
      <Animated.View
        style={{
          width: size,
          height: size,
          borderRadius: size / 2,
          borderWidth: stroke,
          borderColor: track ?? c.border,
          borderTopColor: color ?? c.primary,
          transform: [
            {
              rotate: turn.interpolate({ inputRange: [0, 1], outputRange: ["0deg", "360deg"] }),
            },
          ],
        }}
      />
    </View>
  );
}
