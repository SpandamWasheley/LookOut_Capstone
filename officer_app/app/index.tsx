import { Feather } from "@expo/vector-icons";
import React from "react";
import { StyleSheet, Text, View } from "react-native";

import { Spinner } from "@/components/ui/Spinner";
import { radius, space, type as T } from "@/constants/theme";
import { useColors } from "@/hooks/useColors";

/**
 * What an officer sees while the app works out whether they are signed in.
 *
 * NavigationGuard (in _layout.tsx) redirects away from here as soon as it
 * knows, so this is on screen for a moment — but it is the FIRST moment, and
 * it used to be a bare grey spinner centred on an empty background, which is
 * indistinguishable from an app that has failed to start.
 *
 * Same mark as the launcher icon and the sign-in screen, so the handover from
 * the splash is seamless: the eye is already on screen when the splash lifts.
 */
export default function IndexScreen() {
  const c = useColors();

  return (
    <View style={[styles.root, { backgroundColor: c.background }]}>
      <View style={styles.brand}>
        <View style={[styles.mark, { backgroundColor: c.primary }]}>
          <Feather name="eye" size={32} color={c.primaryForeground} />
        </View>
        <Text style={[T.title, { color: c.foreground }]}>LookOut</Text>
        <Text style={[T.meta, { color: c.mutedForeground }]}>Barangay enforcement</Text>
      </View>
      <Spinner size={22} label="Opening LookOut" />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, alignItems: "center", justifyContent: "center", gap: space.xxxl },
  brand: { alignItems: "center", gap: space.sm },
  mark: {
    width: 64,
    height: 64,
    borderRadius: radius.card,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: space.xs,
  },
});
