import React from "react";
import { ActivityIndicator, View } from "react-native";

import { useColors } from "@/hooks/useColors";

// NavigationGuard (in _layout.tsx) redirects away from here based on auth
// state as soon as it knows whether the officer is logged in.
export default function IndexScreen() {
  const c = useColors();
  return (
    <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: c.background }}>
      <ActivityIndicator color={c.primary} />
    </View>
  );
}
