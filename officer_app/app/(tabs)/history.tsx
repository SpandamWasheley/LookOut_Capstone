import { Feather } from "@expo/vector-icons";
import React from "react";
import { ActivityIndicator, FlatList, Platform, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import AssignmentCard from "@/components/AssignmentCard";
import { useAssignments } from "@/context/AssignmentContext";
import { useColors } from "@/hooks/useColors";

export default function HistoryScreen() {
  const { historyAssignments, loading, error } = useAssignments();
  const c = useColors();
  const insets = useSafeAreaInsets();

  const resolved = historyAssignments.filter((a) => a.status === "resolved");
  const dismissed = historyAssignments.filter((a) => a.status === "acknowledged");

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
          <Text style={[styles.countText, { color: c.mutedForeground }]}>{historyAssignments.length} closed</Text>
        </View>
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
            ) : historyAssignments.length === 0 ? (
              <View style={styles.empty}>
                <Feather name="clock" size={48} color={c.mutedForeground} />
                <Text style={[styles.emptyTitle, { color: c.foreground }]}>No history yet</Text>
                <Text style={[styles.emptyText, { color: c.mutedForeground }]}>Resolved assignments will appear here</Text>
              </View>
            ) : (
              <>
                {resolved.length > 0 && (
                  <>
                    <Text style={[styles.sectionLabel, { color: c.mutedForeground }]}>RESOLVED ({resolved.length})</Text>
                    {resolved.map((a) => (
                      <AssignmentCard key={a.id} assignment={a} />
                    ))}
                  </>
                )}
                {dismissed.length > 0 && (
                  <>
                    <Text style={[styles.sectionLabel, { color: c.mutedForeground, marginTop: 8 }]}>
                      DISMISSED ({dismissed.length})
                    </Text>
                    {dismissed.map((a) => (
                      <AssignmentCard key={a.id} assignment={a} />
                    ))}
                  </>
                )}
              </>
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
  listContent: { paddingHorizontal: 16, paddingTop: 16 },
  sectionLabel: { fontSize: 11, fontFamily: "Inter_600SemiBold", letterSpacing: 0.8, marginBottom: 10 },
  empty: { alignItems: "center", justifyContent: "center", paddingVertical: 80, gap: 12 },
  emptyTitle: { fontSize: 18, fontFamily: "Inter_600SemiBold" },
  emptyText: { fontSize: 14, fontFamily: "Inter_400Regular", textAlign: "center" },
});
