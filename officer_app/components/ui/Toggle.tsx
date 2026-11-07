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
 *
 * THE THUMB CHANGES COLOUR WITH STATE, by the same crossfade, and it has to.
 * It used to be `card` in both states, which in dark mode is #0f2030 on a
 * #142634 track -- two near-identical dark blues, on a card that is #0f2030
 * as well, so an "off" switch was invisible. No single colour fixes that: in
 * dark mode "off" is a DARK track needing a light thumb, while "on" is a
 * light blue track needing a dark one. So the thumb is `mutedForeground`
 * over the muted track and `primaryForeground` over the primary one -- the
 * two tokens that already mean "legible on that background", which is
 * exactly the question being asked.
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
      {/* A hairline, because the off track (#142634) barely separates from
          the card it sits on (#0f2030) in dark mode -- without it the control
          reads as a smudge rather than as a switch. */}
      <View style={[styles.track, { backgroundColor: c.muted, borderColor: c.border }]}>
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
        >
          <View style={[styles.face, { backgroundColor: c.mutedForeground }]} />
          <Animated.View
            style={[styles.face, { backgroundColor: c.primaryForeground, opacity: slide }]}
          />
        </Animated.View>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  track: {
    width: TRACK_W,
    height: TRACK_H,
    borderRadius: TRACK_H / 2,
    justifyContent: "center",
    borderWidth: 1,
  },
  on: { borderRadius: TRACK_H / 2 },
  thumb: { width: THUMB, height: THUMB, borderRadius: THUMB / 2, position: "absolute", left: 0 },
  face: { ...StyleSheet.absoluteFillObject, borderRadius: THUMB / 2 },
});
