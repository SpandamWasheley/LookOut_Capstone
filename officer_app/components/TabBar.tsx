import { Feather } from "@expo/vector-icons";
import type { BottomTabBarProps } from "@react-navigation/bottom-tabs";
import * as Haptics from "expo-haptics";
import React from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { radius, space, type as T } from "@/constants/theme";
import { useAssignments } from "@/context/AssignmentContext";
import { useAuth } from "@/context/AuthContext";
import { useColors } from "@/hooks/useColors";

/** Route name → what the officer sees. Keyed by the file name under app/(tabs). */
const TABS: Record<string, { label: string; icon: keyof typeof Feather.glyphMap; counts?: boolean }> = {
  index: { label: "Assignments", icon: "shield", counts: true },
  history: { label: "History", icon: "clock" },
  profile: { label: "Profile", icon: "user" },
};

/**
 * The bottom bar.
 *
 * Replaces the default Expo Router / React Navigation tab bar so the chrome
 * belongs to this app rather than to the platform. The active tab is marked
 * by tinting its icon and label; the Assignments tab also carries a count of
 * the violations assigned to this officer.
 *
 * This used to hang a "spine" from the top edge over the active tab -- the
 * same 4px bar that runs down an assignment card, reusing the app's one
 * structural device to mean "this one". It was removed deliberately: the
 * count badge now occupies that corner, and two markers competing in the
 * same small space read as clutter rather than as one learnable device.
 */
export function TabBar({ state, navigation }: BottomTabBarProps) {
  const c = useColors();
  const insets = useSafeAreaInsets();
  const { activeAssignments } = useAssignments();
  const { officer } = useAuth();

  /**
   * How many violations are assigned to THIS officer.
   *
   * `activeAssignments` is already everything still open (active +
   * dispatched) across the whole board, so the only filter needed is whose
   * it is -- badging an officer with another officer's workload would be
   * worse than carrying no badge at all.
   *
   * Accepted ones count too. The question this answers is "how many do I
   * have", not "how many have I not got to yet", so it matches the list on
   * the Assignments screen exactly -- badge and screen can never disagree
   * about what is yours.
   */
  const officerId = officer?.officerId ?? null;
  const assigned =
    officerId == null
      ? 0
      : activeAssignments.filter((a) => a.assignedOfficerIds.includes(officerId)).length;

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
            accessibilityLabel={
              tab.counts && assigned > 0 ? `${tab.label}, ${assigned} assigned` : tab.label
            }
            style={({ pressed }) => [styles.tab, { opacity: pressed && !focused ? 0.6 : 1 }]}
          >
            <View>
              <Feather
                name={tab.icon}
                size={21}
                color={focused ? c.primary : c.mutedForeground}
              />
              {tab.counts && assigned > 0 && (
                <View
                  style={[styles.badge, { backgroundColor: c.urgent, borderColor: c.card }]}
                  // Already spoken by the tab's accessibilityLabel above.
                  accessibilityElementsHidden
                  importantForAccessibility="no-hide-descendants"
                >
                  <Text style={styles.badgeText}>{assigned > 99 ? "99+" : assigned}</Text>
                </View>
              )}
            </View>
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
  bar: { flexDirection: "row", borderTopWidth: 1, paddingTop: space.base },
  tab: { flex: 1, alignItems: "center", gap: space.xs },
  label: { marginTop: space.xxs },
  badge: {
    position: "absolute",
    top: -6,
    left: 11,
    minWidth: 17,
    height: 17,
    paddingHorizontal: 4,
    borderRadius: radius.pill,
    // Ringed in the bar's own colour so it reads as sitting ON the icon
    // rather than merging into it.
    borderWidth: 1.5,
    alignItems: "center",
    justifyContent: "center",
  },
  // White, not a token: there is no urgentForeground, and white carries on
  // both themes' urgent (#c0392b light, #ef4444 dark).
  badgeText: {
    color: "#ffffff",
    fontSize: 10,
    lineHeight: 13,
    fontFamily: T.metaStrong.fontFamily,
  },
});
