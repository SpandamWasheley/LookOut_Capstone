import { Feather } from "@expo/vector-icons";
import React from "react";
import { StyleSheet, Text, View } from "react-native";

import { radius, space, type as T } from "@/constants/theme";
import { useColors } from "@/hooks/useColors";

/**
 * An inline message about what just happened.
 *
 * Replaces the error box that login, forgot-password and change-password each
 * carried a copy of. All three were hardcoded to a literal `#fed7d7` pink —
 * which never changed in dark mode, so a failed sign-in at night painted a
 * pastel panel across the screen — and all three drew the icon and text in
 * `c.primary`, which in light mode was the old AMBER brand colour. Amber on
 * pink belonged to neither the brand nor the error.
 */
export function Notice({
  tone,
  message,
}: {
  tone: "error" | "success";
  message: string;
}) {
  const c = useColors();
  const colour = tone === "error" ? c.urgent : c.closed;
  const fill = tone === "error" ? c.urgentSoft : c.closedSoft;

  return (
    <View
      accessibilityRole="alert"
      style={[
        styles.box,
        { backgroundColor: fill, borderColor: colour + "40", borderRadius: radius.control },
      ]}
    >
      <Feather
        name={tone === "error" ? "alert-circle" : "check-circle"}
        size={15}
        color={colour}
      />
      <Text style={[T.metaStrong, styles.text, { color: colour }]}>{message}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  box: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
    borderWidth: 1,
    padding: space.md,
  },
  text: { flex: 1 },
});
