import { Feather } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import React from "react";
import { Platform, Pressable, StyleProp, StyleSheet, Text, View, ViewStyle } from "react-native";

import { radius, space, TOUCH_MIN, type as T } from "@/constants/theme";
import { useColors } from "@/hooks/useColors";
import { Spinner } from "@/components/ui/Spinner";

/**
 * primary  the one action a screen is for — accept, save, sign in
 * quiet    a real alternative that is not the main one — cancel, not now
 * danger   destructive or closing — sign out, dismiss a violation
 */
type Variant = "primary" | "quiet" | "danger";

interface Props {
  label: string;
  onPress: () => void;
  variant?: Variant;
  icon?: keyof typeof Feather.glyphMap;
  /** Swaps the icon for a spinner and blocks presses. The label stays, so the
   *  button does not change width mid-action. */
  loading?: boolean;
  disabled?: boolean;
  /** Full width by default — most of these are the bottom of a form. */
  full?: boolean;
  style?: StyleProp<ViewStyle>;
}

/**
 * The button.
 *
 * Every screen used to roll its own: a Pressable, a hand-written row of icon
 * and text, and `opacity: pressed ? 0.8 : 1` as the entire press state. Twelve
 * of them, no two quite the same height, and a disabled one looked identical
 * to a faded pressed one. This is one component with three jobs, a real
 * disabled state, a loading state that does not resize, and a floor of 48px —
 * Android's minimum target, which several of the old ones missed.
 */
export function Button({
  label,
  onPress,
  variant = "primary",
  icon,
  loading,
  disabled,
  full = true,
  style,
}: Props) {
  const c = useColors();
  const blocked = !!disabled || !!loading;

  const tone = {
    primary: { bg: c.primary, fg: c.primaryForeground, border: "transparent" },
    quiet: { bg: "transparent", fg: c.primary, border: c.border },
    danger: { bg: "transparent", fg: c.urgent, border: c.urgent + "66" },
  }[variant];

  const press = () => {
    if (blocked) return;
    if (Platform.OS !== "web") Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    onPress();
  };

  return (
    <Pressable
      onPress={press}
      disabled={blocked}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: blocked, busy: !!loading }}
      style={({ pressed }) => [
        styles.base,
        {
          backgroundColor: tone.bg,
          borderColor: tone.border,
          alignSelf: full ? "stretch" : "flex-start",
          // Disabled and pressed must not look the same. Disabled drops to
          // 0.4 and stays there; pressed only dips to 0.72 and springs back,
          // which reads as a press rather than as "nothing happened".
          opacity: disabled ? 0.4 : pressed ? 0.72 : 1,
        },
        style,
      ]}
    >
      {loading ? (
        <Spinner size={18} color={tone.fg} track={tone.fg + "33"} label={label} />
      ) : icon ? (
        <Feather name={icon} size={17} color={tone.fg} />
      ) : null}
      <Text style={[T.label, styles.label, { color: tone.fg }]} numberOfLines={1}>
        {label}
      </Text>
      {/* Balances the icon so the label stays optically centred whether or not
          there is one — without this a button gains a left-weighted look the
          moment an icon appears. */}
      {(loading || icon) && !full ? <View style={{ width: space.xs }} /> : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    minHeight: TOUCH_MIN,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: space.sm,
    paddingHorizontal: space.lg,
    borderRadius: radius.control,
    borderWidth: 1,
  },
  label: { fontSize: 15, lineHeight: 20 },
});
