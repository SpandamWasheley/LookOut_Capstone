import { Feather } from "@expo/vector-icons";
import React, { useMemo, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import AssignmentCard from "@/components/AssignmentCard";
import { AutoScrollView } from "@/components/AutoScrollView";
import { AssignmentCardSkeleton } from "@/components/ui/Skeleton";
import { GUTTER, radius, space, SPINE, type as T } from "@/constants/theme";
import { useAssignments } from "@/context/AssignmentContext";
import { useAuth } from "@/context/AuthContext";
import { useColors } from "@/hooks/useColors";

type Filter = "all" | "resolved" | "dismissed";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "resolved", label: "Resolved" },
  { key: "dismissed", label: "Dismissed" },
];

export default function HistoryScreen() {
  const { historyAssignments, loading, error } = useAssignments();
  const { officer } = useAuth();
  const c = useColors();
  const insets = useSafeAreaInsets();

  const [filter, setFilter] = useState<Filter>("all");
  const myId = officer?.officerId;

  const newestFirst = (a: { dispatchedAt: string }, b: { dispatchedAt: string }) =>
    new Date(b.dispatchedAt).getTime() - new Date(a.dispatchedAt).getTime();

  const mine = useMemo(
    () => historyAssignments.filter((a) => myId != null && a.assignedOfficerIds.includes(myId)),
    [historyAssignments, myId],
  );

  const shown = useMemo(() => {
    const byFilter =
      filter === "all"
        ? mine
        : filter === "resolved"
          ? mine.filter((a) => a.status === "resolved")
          : mine.filter((a) => a.status === "acknowledged");
    return [...byFilter].sort(newestFirst);
  }, [mine, filter]);

  return (
    <View style={[styles.root, { backgroundColor: c.background }]}>
      <View
        style={[
          styles.header,
          {
            backgroundColor: c.card,
            borderBottomColor: c.border,
            paddingTop: insets.top + (Platform.OS === "web" ? space.lg : space.md),
          },
        ]}
      >
        <Text style={[T.meta, { color: c.mutedForeground }]}>Everything you have closed</Text>
        <Text style={[T.title, styles.headline, { color: c.foreground }]}>History</Text>

        {/* A segmented control, not three pills. The pills filled the active
            one with the brand colour, which made a filter look like the
            screen's primary action; a segment reads as "one of these three"
            and takes the same space. */}
        <View style={[styles.segments, { backgroundColor: c.secondary, borderColor: c.border }]}>
          {FILTERS.map((option) => {
            const active = filter === option.key;
            return (
              <Pressable
                key={option.key}
                onPress={() => setFilter(option.key)}
                accessibilityRole="tab"
                accessibilityState={{ selected: active }}
                style={({ pressed }) => [
                  styles.segment,
                  {
                    backgroundColor: active ? c.primary : "transparent",
                    opacity: pressed && !active ? 0.6 : 1,
                  },
                ]}
              >
                <Text
                  style={[
                    T.label,
                    { color: active ? c.primaryForeground : c.mutedForeground },
                  ]}
                >
                  {option.label}
                </Text>
              </Pressable>
            );
          })}
        </View>
      </View>

      <AutoScrollView
        contentContainerStyle={{ paddingBottom: insets.bottom + space.xxl }}
        bounces={false}
        overScrollMode="never"
      >
        <View style={styles.list}>
          {loading ? (
            <>
              <AssignmentCardSkeleton />
              <AssignmentCardSkeleton />
            </>
          ) : error ? (
            <View style={styles.empty}>
              <Feather name="wifi-off" size={36} color={c.mutedForeground} />
              <Text style={[T.heading, { color: c.foreground }]}>Can&apos;t reach the server</Text>
              <Text style={[T.body, styles.emptyBody, { color: c.mutedForeground }]}>{error}</Text>
            </View>
          ) : shown.length === 0 ? (
            <View style={styles.empty}>
              <Feather name="clock" size={40} color={c.mutedForeground} />
              <Text style={[T.heading, { color: c.foreground }]}>
                {mine.length === 0 ? "Nothing closed yet" : `No ${filter} violations`}
              </Text>
              <Text style={[T.body, styles.emptyBody, { color: c.mutedForeground }]}>
                {mine.length === 0
                  ? "Violations you resolve or dismiss are kept here."
                  : "Switch the filter to see the rest."}
              </Text>
            </View>
          ) : (
            <>
              <View style={styles.section}>
                <View style={[styles.sectionSpine, { backgroundColor: c.primary }]} />
                <Text style={[T.label, { color: c.foreground }]}>
                  {filter === "all" ? "Closed" : FILTERS.find((f) => f.key === filter)?.label}
                </Text>
                <Text style={[T.metaStrong, styles.count, { color: c.mutedForeground }]}>
                  {shown.length}
                </Text>
              </View>
              {shown.map((a) => (
                <AssignmentCard key={a.id} assignment={a} />
              ))}
            </>
          )}
        </View>
      </AutoScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: { paddingHorizontal: GUTTER, paddingBottom: space.base, borderBottomWidth: 1 },
  headline: { marginTop: space.xxs },
  segments: {
    flexDirection: "row",
    gap: space.xs,
    padding: space.xs,
    borderWidth: 1,
    borderRadius: radius.control + 4,
    marginTop: space.base,
  },
  segment: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: space.sm + 2,
    borderRadius: radius.control,
  },
  list: { paddingHorizontal: GUTTER, paddingTop: space.base },
  section: { flexDirection: "row", alignItems: "center", gap: space.sm, marginBottom: space.md },
  sectionSpine: { width: SPINE, height: 14, borderRadius: 2 },
  count: { marginLeft: "auto" },
  empty: { alignItems: "center", justifyContent: "center", paddingVertical: 72, gap: space.md },
  emptyBody: { textAlign: "center", maxWidth: 300 },
});
