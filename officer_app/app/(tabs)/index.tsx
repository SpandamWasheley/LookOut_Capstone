import { Feather } from "@expo/vector-icons";
import React, { useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Platform,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import AssignmentCard from "@/components/AssignmentCard";
import { useAssignments } from "@/context/AssignmentContext";
import { useAuth } from "@/context/AuthContext";
import { useColors } from "@/hooks/useColors";

const FILTERS = ["All", "Pending", "Assigned"] as const;
type Filter = (typeof FILTERS)[number];

const FILTER_MAP: Record<Filter, string[]> = {
  All: ["active", "dispatched"],
  Pending: ["active"],
  Assigned: ["dispatched"],
};

export default function AssignmentsScreen() {
  const { activeAssignments, loading, error, refreshAssignments } = useAssignments();
  const { officer } = useAuth();
  const c = useColors();
  const insets = useSafeAreaInsets();

  const [filter, setFilter] = useState<Filter>("All");
  const [search, setSearch] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  // Only show assignments dispatched to this officer
  const myAssignments = useMemo(() => {
    const statuses = FILTER_MAP[filter];
    return activeAssignments.filter(
      (a) =>
        statuses.includes(a.status) &&
        officer?.officerId != null &&
        a.assignedOfficerIds.includes(officer.officerId)
    );
  }, [activeAssignments, filter, officer]);

  const searched = useMemo(() => {
    if (!search.trim()) return myAssignments;
    const q = search.toLowerCase();
    return myAssignments.filter(
      (a) =>
        a.violationType.label.toLowerCase().includes(q) ||
        a.location.toLowerCase().includes(q) ||
        a.description?.toLowerCase().includes(q)
    );
  }, [myAssignments, search]);

  const pendingCount = activeAssignments.filter(
    (a) =>
      a.status === "active" &&
      officer?.officerId != null &&
      a.assignedOfficerIds.includes(officer.officerId)
  ).length;

  const lastName = officer?.name?.trim().split(/\s+/).pop() ?? "Officer";

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
          <Text style={[styles.greeting, { color: c.mutedForeground }]}>Welcome Back!</Text>
          <Text style={[styles.title, { color: c.foreground }]} numberOfLines={1}>{lastName}</Text>
        </View>
        {pendingCount > 0 && (
          <View style={[styles.alertBadge, { backgroundColor: c.primary }]}>
            <Feather name="alert-circle" size={14} color="#fff" />
            <Text style={styles.alertBadgeText}>{pendingCount} pending</Text>
          </View>
        )}
      </View>

      {/* Search bar */}
      <View style={[styles.searchRow, { backgroundColor: c.card, borderBottomColor: c.border }]}>
        <View style={[styles.searchBox, { backgroundColor: c.secondary, borderColor: c.border }]}>
          <Feather name="search" size={14} color={c.mutedForeground} />
          <TextInput
            value={search}
            onChangeText={setSearch}
            placeholder="Search violations…"
            placeholderTextColor={c.mutedForeground}
            style={[styles.searchInput, { color: c.foreground }]}
          />
          {search.length > 0 && (
            <Pressable onPress={() => setSearch("")}>
              <Feather name="x" size={14} color={c.mutedForeground} />
            </Pressable>
          )}
        </View>
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
            ) : searched.length === 0 ? (
              <View style={styles.empty}>
                <Feather name="check-circle" size={48} color={c.mutedForeground} />
                <Text style={[styles.emptyTitle, { color: c.foreground }]}>
                  {myAssignments.length === 0 ? "No assignments yet" : "No results"}
                </Text>
                <Text style={[styles.emptyText, { color: c.mutedForeground }]}>
                  {myAssignments.length === 0
                    ? "Waiting for dispatch from command"
                    : "Try a different search term"}
                </Text>
              </View>
            ) : (
              <>
                <Text style={[styles.sectionLabel, { color: c.mutedForeground }]}>
                  YOUR ASSIGNED VIOLATIONS · {searched.length}
                </Text>
                {searched.map((a) => (
                  <AssignmentCard key={a.id} assignment={a} />
                ))}
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
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 20, paddingBottom: 16, borderBottomWidth: 1, gap: 10 },
  greeting: { fontSize: 13, fontFamily: "Inter_400Regular" },
  title: { fontSize: 20, fontFamily: "Inter_700Bold", marginTop: 2 },
  alertBadge: { flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 12, paddingVertical: 6, borderRadius: 20 },
  alertBadgeText: { color: "#fff", fontSize: 13, fontFamily: "Inter_600SemiBold" },
  searchRow: { paddingHorizontal: 16, paddingVertical: 10, borderBottomWidth: 1 },
  searchBox: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 12, paddingVertical: 9, borderRadius: 12, borderWidth: 1 },
  searchInput: { flex: 1, fontSize: 14, fontFamily: "Inter_400Regular", padding: 0 },
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
