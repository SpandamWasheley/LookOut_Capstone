import { Feather } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Platform, Pressable, StyleSheet, Switch, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { AutoScrollView } from "@/components/AutoScrollView";
import { useAssignments } from "@/context/AssignmentContext";
import { useAuth } from "@/context/AuthContext";
import { useTheme, type ThemePreference } from "@/context/ThemeContext";
import { useColors } from "@/hooks/useColors";
import * as api from "@/lib/api";
import { formatPhone, isValidPhone } from "@/lib/phone";

// The three choices, in the order they are offered. "System" FIRST and
// default: a phone that dims itself at dusk should take the app with it, and
// an officer who never opens this setting gets that for free.
const THEME_CHOICES: { value: ThemePreference; label: string; icon: keyof typeof Feather.glyphMap }[] = [
  { value: "system", label: "System", icon: "smartphone" },
  { value: "light", label: "Light", icon: "sun" },
  { value: "dark", label: "Dark", icon: "moon" },
];

export default function ProfileScreen() {
  const { officer: authOfficer, logout } = useAuth();
  const { assignments } = useAssignments();
  const c = useColors();
  const { preference, setPreference } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const [profile, setProfile] = useState<api.ApiOfficer | null>(null);
  const [loading, setLoading] = useState(true);

  // The ACCOUNT, separate from the officer record above: the mobile number and
  // the SMS switch live there, and /auth/me/ is the only route an officer may
  // change them through.
  const [account, setAccount] = useState<api.ApiUser | null>(null);
  const [phone, setPhone] = useState("");
  const [smsAlerts, setSmsAlerts] = useState(false);
  const [savingContact, setSavingContact] = useState(false);
  const [contactError, setContactError] = useState("");
  const [contactSaved, setContactSaved] = useState(false);

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

  const loadAccount = useCallback(async () => {
    try {
      const me = await api.getMe();
      setAccount(me);
      setPhone(formatPhone(me.phone || ""));
      setSmsAlerts(Boolean(me.sms_alerts));
    } catch {
      setAccount(null);
    }
  }, []);

  useEffect(() => {
    loadProfile();
    loadAccount();
  }, [loadProfile, loadAccount]);

  const phoneInvalid = phone.trim() !== "" && !isValidPhone(phone);
  // A switch with no number behind it would store a preference that can never
  // do anything, so the two are saved together or not at all.
  const smsWithoutNumber = smsAlerts && phone.trim() === "";
  const contactDirty = account !== null
    && (phone.trim() !== formatPhone(account.phone || "") || smsAlerts !== Boolean(account.sms_alerts));
  const contactBlocked = !contactDirty || phoneInvalid || smsWithoutNumber || savingContact;

  const handleSaveContact = async () => {
    if (contactBlocked) return;
    setContactError("");
    setContactSaved(false);
    setSavingContact(true);
    try {
      const me = await api.updateMe({ phone: phone.trim(), sms_alerts: smsAlerts });
      // Re-seed from the response: the server keeps the number in one
      // canonical spelling, and showing that back is the confirmation.
      setAccount(me);
      setPhone(formatPhone(me.phone || ""));
      setSmsAlerts(Boolean(me.sms_alerts));
      setContactSaved(true);
      // The roster carries a mirrored copy of the number, so refresh it too.
      loadProfile();
    } catch (err) {
      setContactError(err instanceof Error ? err.message : "Could not save.");
    } finally {
      setSavingContact(false);
    }
  };

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

  if (loading) {
    return (
      <View style={[styles.root, { backgroundColor: c.background, alignItems: "center", justifyContent: "center" }]}>
        <ActivityIndicator color={c.primary} />
      </View>
    );
  }

  if (!authOfficer) return null;

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
            <Text style={[styles.statLabel, { color: c.mutedForeground }]}>Pending</Text>
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

        <View style={[styles.section, { backgroundColor: c.card, borderColor: c.border, marginTop: 8 }]}>
          <Text style={[styles.sectionTitle, { color: c.mutedForeground }]}>DETAILS</Text>
          {[
            { icon: "mail" as const, label: "Email", value: authOfficer.username && profile?.email ? profile.email : "—" },
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

        {/* The number an assignment text would go to, editable here because an
            officer changing SIMs should not need an admin to stay reachable. */}
        <View style={[styles.section, { backgroundColor: c.card, borderColor: c.border }]}>
          <Text style={[styles.sectionTitle, { color: c.mutedForeground }]}>CONTACT</Text>

          <View>
            <Text style={[styles.detailLabel, { color: c.mutedForeground }]}>Mobile number</Text>
            <TextInput
              value={phone}
              onChangeText={(t) => { setPhone(formatPhone(t)); setContactSaved(false); }}
              placeholder="+63 917 123 4567"
              placeholderTextColor={c.mutedForeground}
              keyboardType="phone-pad"
              autoComplete="tel"
              editable={account !== null}
              style={[
                styles.phoneInput,
                {
                  color: c.foreground,
                  backgroundColor: c.muted,
                  borderColor: phoneInvalid ? c.destructive : c.border,
                },
              ]}
            />
            {phoneInvalid && (
              <Text style={[styles.contactHint, { color: c.destructive }]}>
                Enter a mobile number as +63 917 123 4567.
              </Text>
            )}
          </View>

          <View style={styles.smsRow}>
            <View style={styles.detailText}>
              <Text style={[styles.detailValue, { color: c.foreground }]}>SMS alerts</Text>
              <Text style={[styles.contactHint, { color: c.mutedForeground }]}>
                Text me when I am assigned an alert
              </Text>
            </View>
            <Switch
              value={smsAlerts}
              onValueChange={(v) => { setSmsAlerts(v); setContactSaved(false); }}
              disabled={account === null}
              trackColor={{ false: c.border, true: c.primary }}
              accessibilityLabel="SMS alerts"
            />
          </View>

          {smsWithoutNumber && (
            <Text style={[styles.contactHint, { color: c.destructive }]}>
              Add a mobile number to turn on SMS alerts.
            </Text>
          )}
          {contactError !== "" && (
            <Text style={[styles.contactHint, { color: c.destructive }]}>{contactError}</Text>
          )}
          {account === null && (
            <Text style={[styles.contactHint, { color: c.mutedForeground }]}>
              Could not load your account. Pull up again once you are back online.
            </Text>
          )}

          <Pressable
            onPress={handleSaveContact}
            disabled={contactBlocked}
            style={({ pressed }) => [
              styles.saveBtn,
              {
                backgroundColor: c.primary,
                opacity: contactBlocked ? 0.4 : pressed ? 0.8 : 1,
              },
            ]}
          >
            {savingContact ? (
              <ActivityIndicator size="small" color={c.primaryForeground} />
            ) : (
              <Feather
                name={contactSaved && !contactDirty ? "check" : "save"}
                size={15}
                color={c.primaryForeground}
              />
            )}
            <Text style={[styles.saveText, { color: c.primaryForeground }]}>
              {savingContact ? "Saving…" : contactSaved && !contactDirty ? "Saved" : "Save"}
            </Text>
          </Pressable>
        </View>

        <View style={[styles.section, { backgroundColor: c.card, borderColor: c.border }]}>
          <Text style={[styles.sectionTitle, { color: c.mutedForeground }]}>APPEARANCE</Text>
          {/* A segmented control, not a switch: "follow the phone" is a real
              third option and a two-position switch cannot say it. Whichever
              way such a switch booted, the first tap would silently pin the
              app to one theme for good. */}
          <View style={[styles.themeRow, { borderColor: c.border }]}>
            {THEME_CHOICES.map((choice) => {
              const active = preference === choice.value;
              return (
                <Pressable
                  key={choice.value}
                  onPress={() => setPreference(choice.value)}
                  accessibilityRole="radio"
                  accessibilityState={{ selected: active }}
                  accessibilityLabel={`${choice.label} theme`}
                  style={({ pressed }) => [
                    styles.themeOption,
                    {
                      backgroundColor: active ? c.primary : "transparent",
                      opacity: pressed && !active ? 0.6 : 1,
                    },
                  ]}
                >
                  <Feather
                    name={choice.icon}
                    size={15}
                    color={active ? c.primaryForeground : c.mutedForeground}
                  />
                  <Text
                    style={[
                      styles.themeLabel,
                      { color: active ? c.primaryForeground : c.mutedForeground },
                    ]}
                  >
                    {choice.label}
                  </Text>
                </Pressable>
              );
            })}
          </View>
          <Text style={[styles.themeHint, { color: c.mutedForeground }]}>
            {preference === "system"
              ? "Following your phone's light and dark setting."
              : `Always ${preference}, whatever the phone is set to.`}
          </Text>
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
  scroll: { paddingHorizontal: 20, paddingTop: 24, gap: 24 },
  profileCard: { borderRadius: 16, borderWidth: 1, paddingHorizontal: 24, paddingVertical: 28, alignItems: "center", gap: 8 },
  avatar: { width: 72, height: 72, borderRadius: 36, alignItems: "center", justifyContent: "center", marginBottom: 4 },
  avatarText: { color: "#fff", fontSize: 24, fontFamily: "Inter_700Bold" },
  officerName: { fontSize: 20, fontFamily: "Inter_700Bold" },
  rank: { fontSize: 14, fontFamily: "Inter_400Regular" },
  badgePill: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 12, paddingVertical: 5, borderRadius: 20, marginTop: 4 },
  badgeNum: { fontSize: 13, fontFamily: "Inter_600SemiBold" },
  statsRow: { flexDirection: "row", gap: 10, marginTop: 8 },
  statCard: { flex: 1, borderRadius: 12, borderWidth: 1, paddingHorizontal: 16, paddingVertical: 20, alignItems: "center", gap: 4 },
  statNum: { fontSize: 24, fontFamily: "Inter_700Bold" },
  statLabel: { fontSize: 12, fontFamily: "Inter_400Regular" },
  section: { borderRadius: 16, borderWidth: 1, paddingHorizontal: 16, paddingVertical: 20, gap: 12 },
  sectionTitle: { fontSize: 11, fontFamily: "Inter_600SemiBold", letterSpacing: 0.8 },
  detailRow: { flexDirection: "row", alignItems: "flex-start", gap: 12, paddingBottom: 12, borderBottomWidth: 1 },
  themeRow: { flexDirection: "row", borderRadius: 12, borderWidth: 1, padding: 4, gap: 4 },
  themeOption: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 10, borderRadius: 9 },
  themeLabel: { fontSize: 13, fontFamily: "Inter_600SemiBold" },
  themeHint: { fontSize: 12, fontFamily: "Inter_400Regular", lineHeight: 17 },
  phoneInput: {
    marginTop: 6, borderWidth: 1, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 12,
    fontSize: 15, fontFamily: "Inter_500Medium",
  },
  contactHint: { fontSize: 12, fontFamily: "Inter_400Regular", lineHeight: 17, marginTop: 4 },
  smsRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  saveBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8,
    paddingVertical: 14, borderRadius: 12,
  },
  saveText: { fontSize: 15, fontFamily: "Inter_600SemiBold" },
  detailText: { flex: 1 },
  detailLabel: { fontSize: 11, fontFamily: "Inter_400Regular", textTransform: "uppercase", letterSpacing: 0.5 },
  detailValue: { fontSize: 14, fontFamily: "Inter_500Medium", marginTop: 2 },
  logoutBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 18, borderRadius: 12, borderWidth: 1, marginTop: 12 },
  logoutText: { fontSize: 15, fontFamily: "Inter_600SemiBold" },
});
