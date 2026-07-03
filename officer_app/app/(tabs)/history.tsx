import { Feather } from "@expo/vector-icons";
import React, { useMemo, useState } from "react";
import { ActivityIndicator, FlatList, Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import AssignmentCard from "@/components/AssignmentCard";
import { useAssignments } from "@/context/AssignmentContext";
import { useAuth } from "@/context/AuthContext";
import { useColors } from "@/hooks/useColors";

type Filter = "all" | "resolved" | "dismissed";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all",      label: "All" },
  { key: "resolved", label: "Resolved" },
  { key: "dismissed", label: "Dismissed" },
];

export default function HistoryScreen() {
  const { historyAssignments, loading, error } = useAssignments();
  const { officer } = useAuth();
  const c = useColors();
  const insets = useSafeAreaInsets();

  const [filter, setFilter] = useState<Filter>("all");

  const newestFirst = (a: typeof historyAssignments[0], b: typeof historyAssignments[0]) =>
    new Date(b.dispatchedAt).getTime() - new Date(a.dispatchedAt).getTime();

  const myId = officer?.officerId;

  const allHistory = useMemo(() => {
    const mine = historyAssignments.filter(
      (a) => myId != null && a.assignedOfficerIds.includes(myId)
    );

    if (filter === "all") return [...mine].sort(newestFirst);

    if (filter === "resolved")
      return mine.filter((a) => a.status === "resolved").sort(newestFirst);

    return mine.filter((a) => a.status === "acknowledged").sort(newestFirst);
  }, [historyAssignments, myId, filter]);

  return (
    <View style={[styles.root, { backgroundColor: c.background }]}>
      <View
        style={[
          styles.header,
          { backgroundColor: c.card, borderBottomColor: c.border, paddingTop: insets.top + (Platform.OS === "web" ? 12 : 0) },
        ]}
      >
        <Text style={[styles.title, { color: c.foreground }]}>History</Text>
        <View style={[styles.countBadge, { backgroundColor: c.muted }]}>
          <Text style={[styles.countText, { color: c.mutedForeground }]}>{allHistory.length} closed</Text>
        </View>
      </View>

      {/* Filter bar */}
      <View style={[styles.filterRow, { backgroundColor: c.card, borderBottomColor: c.border }]}>
        {FILTERS.map((opt) => (
          <Pressable
            key={opt.key}
            onPress={() => setFilter(opt.key)}
            style={[
              styles.filterChip,
              {
                backgroundColor: filter === opt.key ? c.primary : c.muted,
                borderColor: filter === opt.key ? c.primary : c.border,
              },
            ]}
          >
            <Text style={[styles.filterChipText, { color: filter === opt.key ? "#fff" : c.mutedForeground }]}>
              {opt.label}
            </Text>
          </Pressable>
        ))}
      </View>

      <FlatList
        data={[]}
        renderItem={null}
        ListHeaderComponent={
          <View style={styles.listContent}>
            {loading ? (
              <View style={styles.empty}>
                <ActivityIndicator color={c.primary} />
              </View>
            ) : error ? (
              <View style={styles.empty}>
                <Feather name="alert-triangle" size={40} color={c.destructive} />
                <Text style={[styles.emptyText, { color: c.mutedForeground }]}>{error}</Text>
              </View>
            ) : allHistory.length === 0 ? (
              <View style={styles.empty}>
                <Feather name="clock" size={48} color={c.mutedForeground} />
                <Text style={[styles.emptyTitle, { color: c.foreground }]}>No history yet</Text>
                <Text style={[styles.emptyText, { color: c.mutedForeground }]}>
                  {filter === "all" ? "Resolved and dismissed violations will appear here" : `No ${filter} violations`}
                </Text>
              </View>
            ) : (
              allHistory.map((a) => <AssignmentCard key={a.id} assignment={a} />)
            )}
          </View>
        }
        style={{ flex: 1 }}
        contentContainerStyle={{ paddingBottom: insets.bottom + (Platform.OS === "web" ? 34 : 100) }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 20, paddingBottom: 16, borderBottomWidth: 1 },
  title: { fontSize: 22, fontFamily: "Inter_700Bold" },
  countBadge: { paddingHorizontal: 12, paddingVertical: 5, borderRadius: 20 },
  countText: { fontSize: 13, fontFamily: "Inter_500Medium" },
  filterRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 16, paddingVertical: 10, borderBottomWidth: 1 },
  filterChip: { paddingHorizontal: 14, paddingVertical: 6, borderRadius: 20, borderWidth: 1 },
  filterChipText: { fontSize: 13, fontFamily: "Inter_500Medium" },
  listContent: { paddingHorizontal: 16, paddingTop: 16, gap: 0 },
  empty: { alignItems: "center", justifyContent: "center", paddingVertical: 80, gap: 12 },
  emptyTitle: { fontSize: 18, fontFamily: "Inter_600SemiBold" },
  emptyText: { fontSize: 14, fontFamily: "Inter_400Regular", textAlign: "center" },
});
