import { Feather } from "@expo/vector-icons";
import React, { useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, TextInputProps, View } from "react-native";

import { radius, space, type as T } from "@/constants/theme";
import { useColors } from "@/hooks/useColors";

interface Props extends Omit<TextInputProps, "style"> {
  label: string;
  icon: keyof typeof Feather.glyphMap;
  /** Renders a show/hide eye and manages `secureTextEntry` itself. */
  secret?: boolean;
  /** Draws the border in the destructive colour. The message goes in `hint`. */
  invalid?: boolean;
  hint?: string;
  /** Widens the character spacing — for codes read off a screen digit by digit. */
  spaced?: boolean;
}

/**
 * A labelled text field.
 *
 * Login, forgot-password and change-password each had their own copy of this
 * markup — three label styles, three input rows, three show/hide buttons, and
 * three subtly different paddings. Pulling it into one component is also what
 * made the label change cheap: these were tracked-out capitals (USERNAME,
 * RESET CODE, NEW PASSWORD) in all three files.
 */
export function Field({ label, icon, secret, invalid, hint, spaced, ...input }: Props) {
  const c = useColors();
  const [revealed, setRevealed] = useState(false);

  return (
    <View style={styles.field}>
      <Text style={[T.label, { color: c.foreground }]}>{label}</Text>
      <View
        style={[
          styles.row,
          {
            backgroundColor: c.muted,
            borderColor: invalid ? c.urgent : c.border,
            borderRadius: radius.control,
          },
        ]}
      >
        <Feather name={icon} size={16} color={c.mutedForeground} />
        <TextInput
          placeholderTextColor={c.mutedForeground}
          {...input}
          secureTextEntry={secret ? !revealed : input.secureTextEntry}
          style={[T.body, styles.input, spaced && styles.spaced, { color: c.foreground }]}
        />
        {secret && (
          <Pressable
            onPress={() => setRevealed((v) => !v)}
            accessibilityLabel={revealed ? "Hide password" : "Show password"}
            hitSlop={10}
          >
            <Feather name={revealed ? "eye-off" : "eye"} size={16} color={c.mutedForeground} />
          </Pressable>
        )}
      </View>
      {hint ? (
        <Text style={[T.meta, { color: invalid ? c.urgent : c.mutedForeground }]}>{hint}</Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  field: { gap: space.sm },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.md,
    borderWidth: 1,
    paddingHorizontal: space.md,
    paddingVertical: space.md + 1,
  },
  input: { flex: 1, padding: 0 },
  spaced: { letterSpacing: 4 },
});
