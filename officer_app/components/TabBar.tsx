import { Feather } from "@expo/vector-icons";
import type { BottomTabBarProps } from "@react-navigation/bottom-tabs";
import * as Haptics from "expo-haptics";
import React from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { space, SPINE, type as T } from "@/constants/theme";
import { useColors } from "@/hooks/useColors";

/** Route name → what the officer sees. Keyed by the file name under app/(tabs). */
const TABS: Record<string, { label: string; icon: keyof typeof Feather.glyphMap }> = {
  index: { label: "Assignments", icon: "shield" },
  history: { label: "History", icon: "clock" },
  profile: { label: "Profile", icon: "user" },
};

/**
 * The bottom bar.
 *
 * Replaces the default Expo Router / React Navigation tab bar, which marks the
 * active tab by TINTING ITS ICON — the thing every stock tab bar on both
 * platforms does, and the reason a default tab bar is recognisable as one at a
 * glance. Here the active tab is marked by the spine: the same 4px bar that
 * runs down an assignment card, hung from the top edge of the bar.
 *
 * That is not decoration for its own sake. The spine is this app's one
 * structural device and it always means "this one" — which violation a card
 * is, which section a heading opens, and here, which screen you are on. One
 * device, learned once.
 */
export function TabBar({ state, navigation }: BottomTabBarProps) {
  const c = useColors();
  const insets = useSafeAreaInsets();

  return (
    <View
      style={[
        styles.bar,
        {
          backgroundColor: c.card,
          borderTopColor: c.border,
          // The gesture bar's inset, plus a little, so the labels are not
          // sitting on the handle. Web has no inset and needs the padding
          // outright.
          paddingBottom: insets.bottom + (Platform.OS === "web" ? space.md : space.sm),
        },
      ]}
    >
      {state.routes.map((route, index) => {
        const tab = TABS[route.name];
        if (!tab) return null;
        const focused = state.index === index;

        const press = () => {
          const event = navigation.emit({
            type: "tabPress",
            target: route.key,
            canPreventDefault: true,
          });
          if (focused || event.defaultPrevented) return;
          if (Platform.OS !== "web") Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
          navigation.navigate(route.name as never);
        };

        return (
          <Pressable
            key={route.key}
            onPress={press}
            accessibilityRole="tab"
            accessibilityState={{ selected: focused }}
            accessibilityLabel={tab.label}
            style={({ pressed }) => [styles.tab, { opacity: pressed && !focused ? 0.6 : 1 }]}
          >
            {/* Flush to the bar's top border, so it reads as hanging from the
                edge rather than floating above the icon. */}
            <View
              style={[
                styles.spine,
                { backgroundColor: focused ? c.primary : "transparent" },
              ]}
            />
            <Feather
              name={tab.icon}
              size={21}
              color={focused ? c.primary : c.mutedForeground}
            />
            <Text
              style={[
                T.meta,
                styles.label,
                {
                  color: focused ? c.primary : c.mutedForeground,
                  fontFamily: focused ? T.metaStrong.fontFamily : T.meta.fontFamily,
                },
              ]}
            >
              {tab.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  bar: { flexDirection: "row", borderTopWidth: 1 },
  tab: { flex: 1, alignItems: "center", gap: space.xs },
  spine: {
    width: 26,
    height: SPINE - 1,
    borderBottomLeftRadius: 3,
    borderBottomRightRadius: 3,
    marginBottom: space.sm,
  },
  label: { marginTop: space.xxs },
});
