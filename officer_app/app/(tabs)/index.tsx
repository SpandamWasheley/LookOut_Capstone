import { Feather } from "@expo/vector-icons";
import React, { useMemo, useState } from "react";
import { Platform, Pressable, RefreshControl, StyleSheet, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import AssignmentCard from "@/components/AssignmentCard";
import { AutoScrollView } from "@/components/AutoScrollView";
import { AssignmentCardSkeleton } from "@/components/ui/Skeleton";
import { Button } from "@/components/ui/Button";
import { GUTTER, radius, space, SPINE, type as T } from "@/constants/theme";
import { useAssignments } from "@/context/AssignmentContext";
import { useAuth } from "@/context/AuthContext";
import { useColors } from "@/hooks/useColors";

export default function AssignmentsScreen() {
  const { activeAssignments, loading, error, refreshAssignments } = useAssignments();
  const { officer } = useAuth();
  const c = useColors();
  const insets = useSafeAreaInsets();

  const [search, setSearch] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const mine = useMemo(
    () =>
      activeAssignments.filter(
        (a) =>
          ["active", "dispatched"].includes(a.status) &&
          officer?.officerId != null &&
          a.assignedOfficerIds.includes(officer.officerId),
      ),
    [activeAssignments, officer],
  );

  const searched = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return mine;
    return mine.filter(
      (a) =>
        a.violationType.label.toLowerCase().includes(query) ||
        a.location.toLowerCase().includes(query) ||
        a.description?.toLowerCase().includes(query),
    );
  }, [mine, search]);

  /**
   * The header greets the officer; the workload moved to the tab badge.
   *
   * This screen used to make the count the largest thing on it ("Three need
   * you now"), on the reasoning that it is the only thing an officer opens
   * the app to find out. That reasoning still holds -- the count now rides on
   * the Assignments tab instead, where it is legible from EVERY screen rather
   * than only this one, so it is demoted in size but promoted in reach.
   */
  const greeting = (() => {
    const hour = new Date().getHours();
    if (hour < 12) return "Good morning";
    if (hour < 18) return "Good afternoon";
    return "Good evening";
  })();

  // Given name only. A greeting uses the name someone is actually called by,
  // and the full record ("Spandam Washiley") reads as a database row.
  const firstName = (officer?.name ?? "").trim().split(/\s+/)[0] ?? "";

  // Last two words, so "Spandam Washiley" -> "SW". Same rule as the Profile tab.
  const initials = (officer?.name ?? "")
    .split(" ")
    .filter(Boolean)
    .slice(-2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();

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
          {
            backgroundColor: c.card,
            borderBottomColor: c.border,
            paddingTop: insets.top + (Platform.OS === "web" ? space.lg : space.md),
          },
        ]}
      >
        <View style={styles.identity}>
          <View style={styles.identityText}>
            <Text style={[T.meta, { color: c.mutedForeground }]}>{greeting},</Text>
            <Text
              style={[T.title, styles.headline, { color: c.foreground }]}
              numberOfLines={1}
            >
              Officer {firstName}
            </Text>
          </View>
          <View style={[styles.avatar, { backgroundColor: c.primary }]}>
            <Text style={[T.figure, styles.avatarText, { color: c.primaryForeground }]}>
              {initials || "O"}
            </Text>
          </View>
        </View>

        <View style={[styles.search, { backgroundColor: c.secondary, borderColor: c.border }]}>
          <Feather name="search" size={15} color={c.mutedForeground} />
          <TextInput
            value={search}
            onChangeText={setSearch}
            placeholder="Search your violations"
            placeholderTextColor={c.mutedForeground}
            style={[T.body, styles.searchInput, { color: c.foreground }]}
            returnKeyType="search"
          />
          {search.length > 0 && (
            <Pressable
              onPress={() => setSearch("")}
              accessibilityLabel="Clear search"
              hitSlop={10}
            >
              <Feather name="x" size={15} color={c.mutedForeground} />
            </Pressable>
          )}
        </View>
      </View>

      <AutoScrollView
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={handleRefresh}
            tintColor={c.primary}
            colors={[c.primary]}
            progressBackgroundColor={c.card}
          />
        }
        contentContainerStyle={{
          paddingBottom: insets.bottom + space.xxl,
        }}
        overScrollMode="never"
      >
        <View style={styles.list}>
          {loading ? (
            /* The card's own silhouette rather than a spinner, so the shape
               about to be read is already in place and the screen does not
               jump when the data lands. */
            <>
              <SectionHeading label="Assigned to you" />
              <AssignmentCardSkeleton />
              <AssignmentCardSkeleton />
            </>
          ) : error ? (
            <View style={styles.empty}>
              <Feather name="wifi-off" size={36} color={c.mutedForeground} />
              <Text style={[T.heading, styles.emptyTitle, { color: c.foreground }]}>
                Can&apos;t reach the server
              </Text>
              <Text style={[T.body, styles.emptyBody, { color: c.mutedForeground }]}>
                {error}
              </Text>
              {/* There was no way back from this state before — the screen
                  showed the error and waited for the next 4-second poll. */}
              <Button label="Try again" onPress={refreshAssignments} variant="quiet" full={false} />
            </View>
          ) : searched.length === 0 ? (
            <View style={styles.empty}>
              <Feather
                name={mine.length === 0 ? "check-circle" : "search"}
                size={40}
                color={c.mutedForeground}
              />
              <Text style={[T.heading, styles.emptyTitle, { color: c.foreground }]}>
                {mine.length === 0 ? "Nothing assigned to you" : "No match"}
              </Text>
              <Text style={[T.body, styles.emptyBody, { color: c.mutedForeground }]}>
                {mine.length === 0
                  ? "New violations appear here as dispatch assigns them to you."
                  : `Nothing here matches “${search.trim()}”. Try the violation type or the place.`}
              </Text>
            </View>
          ) : (
            <>
              <SectionHeading label="Assigned to you" count={searched.length} />
              {searched.map((a) => (
                <AssignmentCard key={a.id} assignment={a} />
              ))}
            </>
          )}
        </View>
      </AutoScrollView>
    </View>
  );
}

/**
 * A section heading: the spine, the name in sentence case, the count as data
 * on the right. This replaces `YOUR ASSIGNED VIOLATIONS · 2` — tracked-out
 * capitals with the number welded on by a middle dot.
 */
function SectionHeading({ label, count }: { label: string; count?: number }) {
  const c = useColors();
  return (
    <View style={styles.section}>
      <View style={[styles.sectionSpine, { backgroundColor: c.primary }]} />
      <Text style={[T.label, { color: c.foreground }]}>{label}</Text>
      {count !== undefined && (
        <Text style={[T.metaStrong, styles.sectionCount, { color: c.mutedForeground }]}>
          {count}
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: {
    paddingHorizontal: GUTTER,
    paddingBottom: space.base,
    borderBottomWidth: 1,
  },
  headline: { marginTop: space.xxs },
  identity: { flexDirection: "row", alignItems: "center", gap: space.md },
  identityText: { flex: 1, minWidth: 0 },
  avatar: {
    width: 42,
    height: 42,
    borderRadius: radius.pill,
    alignItems: "center",
    justifyContent: "center",
  },
  avatarText: { fontSize: 14 },
  search: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
    borderWidth: 1,
    borderRadius: radius.control,
    paddingHorizontal: space.md,
    paddingVertical: Platform.OS === "ios" ? space.md : space.sm,
    marginTop: space.base,
  },
  searchInput: { flex: 1, padding: 0 },
  list: { paddingHorizontal: GUTTER, paddingTop: space.base },
  section: { flexDirection: "row", alignItems: "center", gap: space.sm, marginBottom: space.md },
  sectionSpine: { width: SPINE, height: 14, borderRadius: 2 },
  sectionCount: { marginLeft: "auto" },
  empty: { alignItems: "center", justifyContent: "center", paddingVertical: 72, gap: space.md },
  emptyTitle: { marginTop: space.xs },
  emptyBody: { textAlign: "center", maxWidth: 300 },
});
