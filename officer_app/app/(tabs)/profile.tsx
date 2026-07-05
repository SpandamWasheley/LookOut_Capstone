import { Feather } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { useRouter } from "expo-router";
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { AutoScrollView } from "@/components/AutoScrollView";
import { useAssignments } from "@/context/AssignmentContext";
import { useAuth } from "@/context/AuthContext";
import { useColors } from "@/hooks/useColors";
import * as api from "@/lib/api";

const STATUS_OPTIONS: { value: api.ApiOfficer["status"]; label: string; color: string }[] = [
  { value: "on-duty", label: "On Duty", color: "#276749" },
  { value: "responding", label: "On Call", color: "#b7791f" },
  { value: "off-duty", label: "Off Duty", color: "#718096" },
];

export default function ProfileScreen() {
  const { officer: authOfficer, logout } = useAuth();
  const { assignments } = useAssignments();
  const c = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const [profile, setProfile] = useState<api.ApiOfficer | null>(null);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);

  const loadProfile = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getOfficers();
      const list = Array.isArray(res) ? res : res.results;
      const mine = list.find((o) => o.username === authOfficer?.username);
      setProfile(mine ?? null);
    } catch {
      setProfile(null);
    } finally {
      setLoading(false);
    }
  }, [authOfficer?.username]);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  const myAssignments = assignments.filter(
    (a) => authOfficer?.officerId != null && a.assignedOfficerIds.includes(authOfficer.officerId)
  );
  const resolvedCount = myAssignments.filter((a) => a.status === "resolved").length;
  const activeCount = myAssignments.filter((a) => a.status === "active" || a.status === "dispatched").length;
  const dismissedCount = myAssignments.filter((a) => a.status === "acknowledged").length;

  const handleLogout = () => {
    if (Platform.OS === "web") {
      logout();
      return;
    }
    Alert.alert("Sign Out", "Are you sure you want to sign out?", [
      { text: "Cancel", style: "cancel" },
      { text: "Sign Out", style: "destructive", onPress: logout },
    ]);
  };

  const doStatusChange = async (status: api.ApiOfficer["status"]) => {
    if (!profile) return;
    if (Platform.OS !== "web") Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    setUpdating(true);
    try {
      const updated = await api.updateOfficer(profile.id, { status });
      setProfile(updated);
    } catch (err) {
      Alert.alert("Couldn't update status", err instanceof Error ? err.message : "Try again.");
    } finally {
      setUpdating(false);
    }
  };

  const handleStatusChange = (status: api.ApiOfficer["status"]) => {
    if (!profile || profile.status === status) return;
    const labels: Record<string, string> = {
      "on-duty": "On Duty",
      "responding": "On Call",
      "off-duty": "Off Duty",
    };
    if (Platform.OS === "web") {
      if (window.confirm(`Switch status to ${labels[status]}?`)) {
        doStatusChange(status);
      }
    } else {
      Alert.alert(
        "Change Duty Status",
        `Switch to ${labels[status]}?`,
        [
          { text: "Cancel", style: "cancel" },
          { text: "Confirm", onPress: () => doStatusChange(status) },
        ]
      );
    }
  };

  if (loading) {
    return (
      <View style={[styles.root, { backgroundColor: c.background, alignItems: "center", justifyContent: "center" }]}>
        <ActivityIndicator color={c.primary} />
      </View>
    );
  }

  if (!authOfficer) return null;

  const statusConfig = profile ? STATUS_OPTIONS.find((s) => s.value === profile.status) : null;
  const initials = authOfficer.name
    .split(" ")
    .filter(Boolean)
    .slice(-2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();

  return (
    <View style={[styles.root, { backgroundColor: c.background }]}>
      <View
        style={[
          styles.header,
          { backgroundColor: c.card, borderBottomColor: c.border, paddingTop: insets.top + (Platform.OS === "web" ? 20 : 10) },
        ]}
      >
        <Text style={[styles.title, { color: c.foreground }]}>Profile</Text>
      </View>

      <AutoScrollView
        contentContainerStyle={[styles.scroll, { paddingBottom: insets.bottom + (Platform.OS === "web" ? 34 : 32) }]}
        bounces={false}
        overScrollMode="never"
      >
        <View style={[styles.profileCard, { backgroundColor: c.card, borderColor: c.border }]}>
          <View style={[styles.avatar, { backgroundColor: c.primary }]}>
            <Text style={styles.avatarText}>{initials || "O"}</Text>
          </View>
          <Text style={[styles.officerName, { color: c.foreground }]}>{authOfficer.name}</Text>
          <Text style={[styles.rank, { color: c.mutedForeground }]}>{authOfficer.username}</Text>
          {profile && (
            <View style={[styles.badgePill, { backgroundColor: c.muted }]}>
              <Feather name="shield" size={13} color={c.mutedForeground} />
              <Text style={[styles.badgeNum, { color: c.mutedForeground }]}>{profile.badge || profile.code}</Text>
            </View>
          )}
        </View>

        <View style={styles.statsRow}>
          <View style={[styles.statCard, { backgroundColor: c.card, borderColor: c.border }]}>
            <Text style={[styles.statNum, { color: c.foreground }]}>{activeCount}</Text>
            <Text style={[styles.statLabel, { color: c.mutedForeground }]}>Active</Text>
          </View>
          <View style={[styles.statCard, { backgroundColor: c.card, borderColor: c.border }]}>
            <Text style={[styles.statNum, { color: c.foreground }]}>{resolvedCount}</Text>
            <Text style={[styles.statLabel, { color: c.mutedForeground }]}>Resolved</Text>
          </View>
          <View style={[styles.statCard, { backgroundColor: c.card, borderColor: c.border }]}>
            <Text style={[styles.statNum, { color: c.foreground }]}>{dismissedCount}</Text>
            <Text style={[styles.statLabel, { color: c.mutedForeground }]}>Dismissed</Text>
          </View>
        </View>

        {profile && (
          <View style={[styles.section, { backgroundColor: c.card, borderColor: c.border }]}>
            <Text style={[styles.sectionTitle, { color: c.mutedForeground }]}>DUTY STATUS</Text>
            <View style={styles.statusOptions}>
              {STATUS_OPTIONS.map((opt) => (
                <Pressable
                  key={opt.value}
                  onPress={() => handleStatusChange(opt.value)}
                  disabled={updating}
                  style={({ pressed }) => [
                    styles.statusOpt,
                    {
                      backgroundColor: profile.status === opt.value ? `${opt.color}22` : c.muted,
                      borderColor: profile.status === opt.value ? opt.color : c.border,
                      opacity: pressed || updating ? 0.7 : 1,
                    },
                  ]}
                >
                  <View style={[styles.statusDot, { backgroundColor: opt.color }]} />
                  <Text
                    style={[
                      styles.statusOptText,
                      {
                        color: profile.status === opt.value ? opt.color : c.mutedForeground,
                        fontFamily: profile.status === opt.value ? "Inter_600SemiBold" : "Inter_400Regular",
                      },
                    ]}
                  >
                    {opt.label}
                  </Text>
                </Pressable>
              ))}
            </View>
          </View>
        )}

        <View style={[styles.section, { backgroundColor: c.card, borderColor: c.border }]}>
          <Text style={[styles.sectionTitle, { color: c.mutedForeground }]}>DETAILS</Text>
          {[
            { icon: "mail" as const, label: "Email", value: authOfficer.username && profile?.email ? profile.email : "—" },
            { icon: "phone" as const, label: "Contact", value: profile?.phone || "—" },
            { icon: "map-pin" as const, label: "Station", value: "Brgy. Tetuan, Zamboanga City" },
          ].map((row) => (
            <View key={row.label} style={[styles.detailRow, { borderBottomColor: c.border }]}>
              <Feather name={row.icon} size={16} color={c.mutedForeground} />
              <View style={styles.detailText}>
                <Text style={[styles.detailLabel, { color: c.mutedForeground }]}>{row.label}</Text>
                <Text style={[styles.detailValue, { color: c.foreground }]}>{row.value}</Text>
              </View>
            </View>
          ))}
        </View>

        <Pressable
          onPress={handleLogout}
          style={({ pressed }) => [styles.logoutBtn, { borderColor: c.destructive, opacity: pressed ? 0.7 : 1 }]}
        >
          <Feather name="log-out" size={16} color={c.destructive} />
          <Text style={[styles.logoutText, { color: c.destructive }]}>Sign Out</Text>
        </Pressable>
      </AutoScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: { paddingHorizontal: 20, paddingBottom: 16, borderBottomWidth: 1 },
  title: { fontSize: 22, fontFamily: "Inter_700Bold" },
  scroll: { paddingHorizontal: 16, paddingTop: 20, gap: 14 },
  profileCard: { borderRadius: 16, borderWidth: 1, padding: 24, alignItems: "center", gap: 8 },
  avatar: { width: 72, height: 72, borderRadius: 36, alignItems: "center", justifyContent: "center", marginBottom: 4 },
  avatarText: { color: "#fff", fontSize: 24, fontFamily: "Inter_700Bold" },
  officerName: { fontSize: 20, fontFamily: "Inter_700Bold" },
  rank: { fontSize: 14, fontFamily: "Inter_400Regular" },
  badgePill: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 12, paddingVertical: 5, borderRadius: 20, marginTop: 4 },
  badgeNum: { fontSize: 13, fontFamily: "Inter_600SemiBold" },
  statsRow: { flexDirection: "row", gap: 10 },
  statCard: { flex: 1, borderRadius: 12, borderWidth: 1, padding: 16, alignItems: "center", gap: 4 },
  statNum: { fontSize: 24, fontFamily: "Inter_700Bold" },
  statLabel: { fontSize: 12, fontFamily: "Inter_400Regular" },
  section: { borderRadius: 16, borderWidth: 1, padding: 16, gap: 12 },
  sectionTitle: { fontSize: 11, fontFamily: "Inter_600SemiBold", letterSpacing: 0.8 },
  statusOptions: { flexDirection: "row", gap: 8 },
  statusOpt: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 10, borderRadius: 10, borderWidth: 1 },
  statusDot: { width: 8, height: 8, borderRadius: 4 },
  statusOptText: { fontSize: 13 },
  detailRow: { flexDirection: "row", alignItems: "flex-start", gap: 12, paddingBottom: 12, borderBottomWidth: 1 },
  detailText: { flex: 1 },
  detailLabel: { fontSize: 11, fontFamily: "Inter_400Regular", textTransform: "uppercase", letterSpacing: 0.5 },
  detailValue: { fontSize: 14, fontFamily: "Inter_500Medium", marginTop: 2 },
  logoutBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 14, borderRadius: 12, borderWidth: 1, marginTop: 4 },
  logoutText: { fontSize: 15, fontFamily: "Inter_600SemiBold" },
});
