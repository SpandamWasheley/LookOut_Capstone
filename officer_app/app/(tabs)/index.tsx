import { Feather } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import React, { useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Platform,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import AssignmentCard from "@/components/AssignmentCard";
import { useAssignments } from "@/context/AssignmentContext";
import { useAuth } from "@/context/AuthContext";
import { useColors } from "@/hooks/useColors";

const FILTERS = ["All", "Pending", "Accepted"] as const;
type Filter = (typeof FILTERS)[number];

const FILTER_MAP: Record<Filter, string[]> = {
  All: ["active", "dispatched"],
  Pending: ["active"],
  Accepted: ["dispatched"],
};

export default function AssignmentsScreen() {
  const { activeAssignments, loading, error, refreshAssignments } = useAssignments();
  const { officer, logout } = useAuth();
  const c = useColors();
  const insets = useSafeAreaInsets();

  const handleLogout = () => {
    if (Platform.OS !== "web") Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    if (Platform.OS === "web") {
      logout();
      return;
    }
    Alert.alert("Sign Out", "Are you sure you want to sign out?", [
      { text: "Cancel", style: "cancel" },
      { text: "Sign Out", style: "destructive", onPress: logout },
    ]);
  };

  const [filter, setFilter] = useState<Filter>("All");
  const [refreshing, setRefreshing] = useState(false);

  const filtered = useMemo(() => {
    const statuses = FILTER_MAP[filter];
    return activeAssignments.filter((a) => statuses.includes(a.status));
  }, [activeAssignments, filter]);

  const myAssignments = filtered.filter((a) => a.assignedOfficerName === officer?.name);
  const otherAssignments = filtered.filter((a) => a.assignedOfficerName !== officer?.name);

  const pendingCount = activeAssignments.filter((a) => a.status === "active").length;

  const handleRefresh = async () => {
    setRefreshing(true);
    await refreshAssignments();
    setRefreshing(false);
  };

  return (
    <View style={[styles.root, { backgroundColor: c.background }]}>
      <View
        style={[
          styles.header,
          { backgroundColor: c.card, borderBottomColor: c.border, paddingTop: insets.top + (Platform.OS === "web" ? 12 : 0) },
        ]}
      >
        <View style={{ flex: 1 }}>
          <Text style={[styles.greeting, { color: c.mutedForeground }]}>Welcome back</Text>
          <Text style={[styles.name, { color: c.foreground }]}>{officer?.name ?? "Officer"}</Text>
        </View>
        {pendingCount > 0 && (
          <View style={[styles.alertBadge, { backgroundColor: c.primary }]}>
            <Feather name="alert-circle" size={14} color="#fff" />
            <Text style={styles.alertBadgeText}>{pendingCount} pending</Text>
          </View>
        )}
        <Pressable
          onPress={handleLogout}
          style={({ pressed }) => [styles.logoutBtn, { borderColor: c.border, backgroundColor: c.muted, opacity: pressed ? 0.7 : 1 }]}
        >
          <Feather name="log-out" size={16} color={c.mutedForeground} />
        </Pressable>
      </View>

      <View style={[styles.filterRow, { backgroundColor: c.card, borderBottomColor: c.border }]}>
        <FlatList
          horizontal
          data={FILTERS as unknown as Filter[]}
          keyExtractor={(item) => item}
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.filterList}
          renderItem={({ item }) => (
            <Pressable
              onPress={() => setFilter(item)}
              style={[
                styles.filterChip,
                { backgroundColor: filter === item ? c.primary : c.muted, borderColor: filter === item ? c.primary : c.border },
              ]}
            >
              <Text style={[styles.filterText, { color: filter === item ? "#fff" : c.mutedForeground }]}>{item}</Text>
            </Pressable>
          )}
        />
      </View>

      <FlatList
        data={[]}
        renderItem={null}
        ListHeaderComponent={
          <View style={styles.listContent}>
            {loading ? (
              <View style={styles.empty}>
                <ActivityIndicator color={c.primary} />
                <Text style={[styles.emptyText, { color: c.mutedForeground }]}>Loading assignments…</Text>
              </View>
            ) : error ? (
              <View style={styles.empty}>
                <Feather name="alert-triangle" size={40} color={c.destructive} />
                <Text style={[styles.emptyTitle, { color: c.foreground }]}>Couldn't load assignments</Text>
                <Text style={[styles.emptyText, { color: c.mutedForeground }]}>{error}</Text>
              </View>
            ) : filtered.length === 0 ? (
              <View style={styles.empty}>
                <Feather name="check-circle" size={48} color={c.mutedForeground} />
                <Text style={[styles.emptyTitle, { color: c.foreground }]}>All clear</Text>
                <Text style={[styles.emptyText, { color: c.mutedForeground }]}>No active assignments in this category</Text>
              </View>
            ) : (
              <>
                {myAssignments.length > 0 && (
                  <>
                    <Text style={[styles.sectionLabel, { color: c.mutedForeground }]}>YOUR ASSIGNMENTS</Text>
                    {myAssignments.map((a) => (
                      <AssignmentCard key={a.id} assignment={a} />
                    ))}
                  </>
                )}
                {otherAssignments.length > 0 && (
                  <>
                    <Text style={[styles.sectionLabel, { color: c.mutedForeground }]}>UNIT ASSIGNMENTS</Text>
                    {otherAssignments.map((a) => (
                      <AssignmentCard key={a.id} assignment={a} />
                    ))}
                  </>
                )}
              </>
            )}
          </View>
        }
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} tintColor={c.primary} />}
        style={{ flex: 1 }}
        contentContainerStyle={{ paddingBottom: insets.bottom + (Platform.OS === "web" ? 34 : 100) }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 20, paddingBottom: 16, borderBottomWidth: 1 },
  greeting: { fontSize: 13, fontFamily: "Inter_400Regular" },
  name: { fontSize: 20, fontFamily: "Inter_700Bold", marginTop: 2 },
  alertBadge: { flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 12, paddingVertical: 6, borderRadius: 20 },
  logoutBtn: { width: 36, height: 36, borderRadius: 10, borderWidth: 1, alignItems: "center", justifyContent: "center", marginLeft: 6 },
  alertBadgeText: { color: "#fff", fontSize: 13, fontFamily: "Inter_600SemiBold" },
  filterRow: { borderBottomWidth: 1 },
  filterList: { paddingHorizontal: 16, paddingVertical: 10, gap: 8 },
  filterChip: { paddingHorizontal: 14, paddingVertical: 6, borderRadius: 20, borderWidth: 1 },
  filterText: { fontSize: 13, fontFamily: "Inter_500Medium" },
  listContent: { paddingHorizontal: 16, paddingTop: 16 },
  sectionLabel: { fontSize: 11, fontFamily: "Inter_600SemiBold", letterSpacing: 0.8, marginBottom: 10, marginTop: 6 },
  empty: { alignItems: "center", justifyContent: "center", paddingVertical: 80, gap: 12 },
  emptyTitle: { fontSize: 18, fontFamily: "Inter_600SemiBold" },
  emptyText: { fontSize: 14, fontFamily: "Inter_400Regular", textAlign: "center" },
});
