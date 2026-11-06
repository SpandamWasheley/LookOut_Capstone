import * as Haptics from "expo-haptics";
import React, { useEffect, useRef } from "react";
import { Animated, Platform, Pressable, StyleSheet, View } from "react-native";

import { duration } from "@/constants/theme";
import { useColors } from "@/hooks/useColors";

const TRACK_W = 52;
const TRACK_H = 32;
const THUMB = 26;
const INSET = (TRACK_H - THUMB) / 2;

interface Props {
  value: boolean;
  onValueChange: (next: boolean) => void;
  disabled?: boolean;
  accessibilityLabel?: string;
}

/**
 * The on/off switch.
 *
 * Replaces react-native's Switch, which is drawn by the platform: on Android
 * it is Material's switch, in Material's proportions and Material's ripple,
 * and `trackColor` was the only thing about it this app could set. One stock
 * control is all it takes for a screen to read as a template, and this one sat
 * directly beneath hand-built fields on the profile screen.
 *
 * Two things move: the thumb slides, and the "on" track fades in OVER the
 * "off" track rather than the colour being interpolated. That is deliberate —
 * backgroundColor cannot run on the native driver, so animating it would put
 * this on the JS thread and make it stutter whenever a poll lands. Opacity and
 * translateX both can, so the whole gesture stays at 60fps.
 */
export function Toggle({ value, onValueChange, disabled, accessibilityLabel }: Props) {
  const c = useColors();
  const slide = useRef(new Animated.Value(value ? 1 : 0)).current;

  useEffect(() => {
    Animated.timing(slide, {
      toValue: value ? 1 : 0,
      duration: duration.base,
      useNativeDriver: true,
    }).start();
  }, [value, slide]);

  const press = () => {
    if (disabled) return;
    // The haptic fires on the press, not after the state settles: the tap is
    // what the officer did, and confirming it a frame later feels laggy.
    if (Platform.OS !== "web") Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    onValueChange(!value);
  };

  return (
    <Pressable
      onPress={press}
      disabled={disabled}
      accessibilityRole="switch"
      accessibilityState={{ checked: value, disabled: !!disabled }}
      accessibilityLabel={accessibilityLabel}
      // The control is 32 tall but the target is 48 — the minimum anywhere in
      // this app, because it is used one-handed and often in a hurry.
      hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
      style={{ opacity: disabled ? 0.4 : 1 }}
    >
      <View style={[styles.track, { backgroundColor: c.muted }]}>
        <Animated.View
          style={[
            StyleSheet.absoluteFill,
            styles.on,
            { backgroundColor: c.primary, opacity: slide },
          ]}
        />
        <Animated.View
          style={[
            styles.thumb,
            {
              backgroundColor: c.card,
              transform: [
                {
                  translateX: slide.interpolate({
                    inputRange: [0, 1],
                    outputRange: [INSET, TRACK_W - THUMB - INSET],
                  }),
                },
              ],
            },
          ]}
        />
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  track: { width: TRACK_W, height: TRACK_H, borderRadius: TRACK_H / 2, justifyContent: "center" },
  on: { borderRadius: TRACK_H / 2 },
  thumb: { width: THUMB, height: THUMB, borderRadius: THUMB / 2, position: "absolute", left: 0 },
});
