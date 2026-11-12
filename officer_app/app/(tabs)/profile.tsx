import { Feather } from "@expo/vector-icons";
import React, { useCallback, useEffect, useState } from "react";
import { Linking, Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { AutoScrollView } from "@/components/AutoScrollView";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { Toggle } from "@/components/ui/Toggle";
import { GUTTER, radius, space, SPINE, type as T } from "@/constants/theme";
import { useAssignments } from "@/context/AssignmentContext";
import { useAuth } from "@/context/AuthContext";
import { useTheme, type ThemePreference } from "@/context/ThemeContext";
import { useColors } from "@/hooks/useColors";
import * as api from "@/lib/api";
import { formatPhone, isValidPhone } from "@/lib/phone";
import { currentPushStatus, registerForPush, type PushStatus } from "@/lib/notifications";

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

  const [profile, setProfile] = useState<api.ApiOfficer | null>(null);
  const [loading, setLoading] = useState(true);
  const [signOutAsked, setSignOutAsked] = useState(false);
  // "unknown" until the first check comes back, so the row says
  // "Checking…" rather than flashing "Alerts are off" at an officer
  // whose notifications are in fact working.
  const [pushStatus, setPushStatus] = useState<PushStatus | "unknown">("unknown");

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
      setProfile(list.find((o) => o.username === authOfficer?.username) ?? null);
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

  // Re-registering rather than only reading the permission: an officer who
  // has just turned notifications back on in phone settings needs the server
  // to be told, and this is the screen they would come to afterwards.
  const checkPush = useCallback(async () => {
    const status = await currentPushStatus();
    setPushStatus(status === "registered" ? status : await registerForPush());
  }, []);

  useEffect(() => {
    loadProfile();
    loadAccount();
    checkPush();
  }, [loadProfile, loadAccount, checkPush]);

  const phoneInvalid = phone.trim() !== "" && !isValidPhone(phone);
  // A switch with no number behind it would store a preference that can never
  // do anything, so the two are saved together or not at all.
  const smsWithoutNumber = smsAlerts && phone.trim() === "";
  const contactDirty =
    account !== null &&
    (phone.trim() !== formatPhone(account.phone || "") || smsAlerts !== Boolean(account.sms_alerts));
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
      setContactError(err instanceof Error ? err.message : "Could not save. Check your signal and try again.");
    } finally {
      setSavingContact(false);
    }
  };

  const mine = assignments.filter(
    (a) => authOfficer?.officerId != null && a.assignedOfficerIds.includes(authOfficer.officerId),
  );
  const counts = [
    { n: mine.filter((a) => a.status === "active" || a.status === "dispatched").length, label: "Open" },
    { n: mine.filter((a) => a.status === "resolved").length, label: "Resolved" },
    { n: mine.filter((a) => a.status === "acknowledged").length, label: "Dismissed" },
  ];

  if (loading) {
    return (
      <View style={[styles.root, styles.centre, { backgroundColor: c.background }]}>
        <Spinner label="Loading your profile" />
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
          {
            backgroundColor: c.card,
            borderBottomColor: c.border,
            paddingTop: insets.top + (Platform.OS === "web" ? space.lg : space.md),
          },
        ]}
      >
        <Text style={[T.meta, { color: c.mutedForeground }]}>Your account and settings</Text>
        <Text style={[T.title, styles.headline, { color: c.foreground }]}>Profile</Text>
      </View>

      <AutoScrollView
        contentContainerStyle={[styles.scroll, { paddingBottom: insets.bottom + space.xxl }]}
        bounces={false}
        overScrollMode="never"
      >
        <View
          style={[
            styles.identity,
            { backgroundColor: c.card, borderColor: c.border, borderRadius: radius.card },
            c.lift(1),
          ]}
        >
          <View style={[styles.avatar, { backgroundColor: c.primary }]}>
            <Text style={[T.figure, { color: c.primaryForeground, fontSize: 22 }]}>
              {initials || "O"}
            </Text>
          </View>
          <Text style={[T.heading, { color: c.foreground }]}>{authOfficer.name}</Text>
          <Text style={[T.meta, { color: c.mutedForeground }]}>{authOfficer.username}</Text>
          {profile && (
            <View style={[styles.badge, { backgroundColor: c.muted }]}>
              <Feather name="shield" size={13} color={c.mutedForeground} />
              <Text style={[T.code, { color: c.mutedForeground }]}>
                {profile.badge || profile.code}
              </Text>
            </View>
          )}
        </View>

        <View style={styles.counts}>
          {counts.map((item) => (
            <View
              key={item.label}
              style={[
                styles.countCard,
                { backgroundColor: c.card, borderColor: c.border, borderRadius: radius.card },
              ]}
            >
              {/* Tabular figures: three numbers side by side should line up on
                  their digits, not drift because a 1 is narrower than a 4. */}
              <Text style={[T.figure, { color: c.foreground }]}>{item.n}</Text>
              <Text style={[T.meta, { color: c.mutedForeground }]}>{item.label}</Text>
            </View>
          ))}
        </View>

        <Section title="Details">
          {[
            { icon: "mail" as const, label: "Email", value: profile?.email || "—" },
            { icon: "map-pin" as const, label: "Station", value: "Brgy. Tetuan, Zamboanga City" },
          ].map((row) => (
            <View key={row.label} style={[styles.detail, { borderBottomColor: c.border }]}>
              <Feather name={row.icon} size={16} color={c.mutedForeground} />
              <View style={styles.detailText}>
                <Text style={[T.meta, { color: c.mutedForeground }]}>{row.label}</Text>
                <Text style={[T.bodyStrong, { color: c.foreground }]}>{row.value}</Text>
              </View>
            </View>
          ))}
        </Section>

        {/* The number an assignment text would go to, editable here because an
            officer changing SIMs should not need an admin to stay reachable. */}
        <Section title="Contact">
          <View>
            <Text style={[T.meta, { color: c.mutedForeground }]}>Mobile number</Text>
            <TextInput
              value={phone}
              onChangeText={(t) => {
                setPhone(formatPhone(t));
                setContactSaved(false);
              }}
              placeholder="+63 917 123 4567"
              placeholderTextColor={c.mutedForeground}
              keyboardType="phone-pad"
              autoComplete="tel"
              editable={account !== null}
              style={[
                T.bodyStrong,
                styles.input,
                {
                  color: c.foreground,
                  backgroundColor: c.muted,
                  borderColor: phoneInvalid ? c.urgent : c.border,
                  borderRadius: radius.control,
                },
              ]}
            />
            {phoneInvalid && (
              <Text style={[T.meta, styles.hint, { color: c.urgent }]}>
                Write the number as +63 917 123 4567.
              </Text>
            )}
          </View>

          {/* This DOES send now (core/sms.py, via UniSMS), so the copy says so
              — it used to read "No texts are sent yet" while no provider was
              wired up, and leaving that there would have been a lie.

              What it must still not promise is DELIVERY. Three things gate a
              text and two of them are invisible from this screen: the barangay's
              system-wide SMS switch, and whether the account has credits. The
              shared sender ID also reaches Globe and DITO only, so an officer
              on Smart/TNT/Sun will never receive one. Hence "when available"
              and the explicit pointer to push, which is the channel that is
              always attempted.

              It also must not promise that every assignment arrives the moment
              it is made. Only a dangerous weapon or an obstruction texts immediately
              (core/sms.py's URGENT_TYPE_CODES); the rest are counted into a
              half-hourly digest, so an officer assigned a smoking violation
              gets a text saying how many are theirs, not one per violation.
              Saying "right away" would make a batched text read as a fault. */}
          <View style={styles.switchRow}>
            <View style={styles.detailText}>
              <Text style={[T.bodyStrong, { color: c.foreground }]}>Allow texts to this number</Text>
              <Text style={[T.meta, styles.hint, { color: c.mutedForeground }]}>
                Get assignments by text when available, on Globe and DITO numbers.
                Urgent ones arrive straight away; the rest are summed up every
                half hour. Push notifications are the main alert either way.
              </Text>
            </View>
            <Toggle
              value={smsAlerts}
              onValueChange={(v) => {
                setSmsAlerts(v);
                setContactSaved(false);
              }}
              disabled={account === null}
              accessibilityLabel="Allow texts to this number"
            />
          </View>

          {smsWithoutNumber && (
            <Text style={[T.meta, { color: c.urgent }]}>
              Add a mobile number first — there is nothing to give permission for.
            </Text>
          )}
          {contactError !== "" && <Text style={[T.meta, { color: c.urgent }]}>{contactError}</Text>}
          {account === null && (
            <Text style={[T.meta, { color: c.mutedForeground }]}>
              Your account details didn&apos;t load. Pull down to retry once you have signal.
            </Text>
          )}

          <Button
            label={savingContact ? "Saving" : contactSaved && !contactDirty ? "Saved" : "Save changes"}
            onPress={handleSaveContact}
            icon={contactSaved && !contactDirty ? "check" : "save"}
            loading={savingContact}
            disabled={contactBlocked && !savingContact}
          />
        </Section>

        {/* Push fails silently by design — a declined permission just means
            the phone never makes a sound — and silence is indistinguishable
            from "no assignments". So the state is stated plainly, with the one
            action that fixes each case. */}
        <Section title="Notifications">
          <View style={styles.switchRow}>
            <Feather
              name={pushStatus === "registered" ? "bell" : "bell-off"}
              size={18}
              color={pushStatus === "registered" ? c.closed : c.mutedForeground}
            />
            <View style={styles.detailText}>
              <Text style={[T.bodyStrong, { color: c.foreground }]}>
                {pushStatus === "registered" ? "Alerts are on" : "Alerts are off"}
              </Text>
              <Text style={[T.meta, styles.hint, { color: c.mutedForeground }]}>
                {{
                  registered: "This phone buzzes when dispatch assigns you a violation, even with the app closed.",
                  denied: "Notifications are blocked for LookOut in your phone's settings.",
                  unsupported: "This device can't receive notifications. Sign in on a phone to get them.",
                  failed: "This phone couldn't register for notifications. Check your signal.",
                  unknown: "Checking…",
                }[pushStatus]}
              </Text>
            </View>
          </View>

          {pushStatus === "denied" && (
            <Button
              label="Open phone settings"
              onPress={() => Linking.openSettings()}
              variant="quiet"
              icon="settings"
            />
          )}
          {pushStatus === "failed" && (
            <Button label="Try again" onPress={checkPush} variant="quiet" icon="refresh-cw" />
          )}
        </Section>

        <Section title="Appearance">
          {/* A segmented control, not a switch: "follow the phone" is a real
              third option and a two-position switch cannot say it. Whichever
              way such a switch booted, the first tap would silently pin the
              app to one theme for good. */}
          <View style={[styles.segments, { backgroundColor: c.secondary, borderColor: c.border }]}>
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
                    styles.segment,
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
                    style={[T.label, { color: active ? c.primaryForeground : c.mutedForeground }]}
                  >
                    {choice.label}
                  </Text>
                </Pressable>
              );
            })}
          </View>
          <Text style={[T.meta, { color: c.mutedForeground }]}>
            {preference === "system"
              ? "Following your phone's light and dark setting."
              : `Always ${preference}, whatever the phone is set to.`}
          </Text>
        </Section>

        <Button
          label="Sign out"
          onPress={() => setSignOutAsked(true)}
          variant="danger"
          icon="log-out"
          style={styles.signOut}
        />
      </AutoScrollView>

      {/* Replaces Alert.alert, which drew Android's own panel — Roboto,
          Material blue, capitalised buttons — over a screen built by hand. */}
      <Dialog
        visible={signOutAsked}
        title="Sign out?"
        message="You'll need your username and password to get back in."
        confirmLabel="Sign out"
        cancelLabel="Stay signed in"
        tone="danger"
        onConfirm={() => {
          setSignOutAsked(false);
          logout();
        }}
        onCancel={() => setSignOutAsked(false)}
      />
    </View>
  );
}

/**
 * A settings group. The heading is sentence case against a spine — the app's
 * one structural device — rather than the tracked-out capitals (DETAILS,
 * CONTACT, APPEARANCE) this screen used to shout three times.
 */
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const c = useColors();
  return (
    <View style={styles.section}>
      <View style={styles.sectionHead}>
        <View style={[styles.sectionSpine, { backgroundColor: c.primary }]} />
        <Text style={[T.label, { color: c.foreground }]}>{title}</Text>
      </View>
      <View
        style={[
          styles.sectionBody,
          { backgroundColor: c.card, borderColor: c.border, borderRadius: radius.card },
        ]}
      >
        {children}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  centre: { alignItems: "center", justifyContent: "center" },
  header: { paddingHorizontal: GUTTER, paddingBottom: space.base, borderBottomWidth: 1 },
  headline: { marginTop: space.xxs },
  scroll: { paddingHorizontal: GUTTER, paddingTop: space.xl, gap: space.xl },

  identity: { borderWidth: 1, paddingVertical: space.xl, alignItems: "center", gap: space.xs },
  avatar: {
    width: 68,
    height: 68,
    borderRadius: radius.pill,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: space.sm,
  },
  badge: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.xs,
    paddingHorizontal: space.md,
    paddingVertical: space.xs + 1,
    borderRadius: radius.pill,
    marginTop: space.sm,
  },

  counts: { flexDirection: "row", gap: space.md },
  countCard: {
    flex: 1,
    borderWidth: 1,
    paddingVertical: space.lg,
    alignItems: "center",
    gap: space.xxs,
  },

  section: { gap: space.md },
  sectionHead: { flexDirection: "row", alignItems: "center", gap: space.sm },
  sectionSpine: { width: SPINE, height: 14, borderRadius: 2 },
  sectionBody: { borderWidth: 1, padding: space.base, gap: space.base },

  detail: { flexDirection: "row", alignItems: "flex-start", gap: space.md, paddingBottom: space.md, borderBottomWidth: 1 },
  detailText: { flex: 1, gap: space.xxs },
  input: { marginTop: space.sm, borderWidth: 1, paddingHorizontal: space.md, paddingVertical: space.md },
  hint: { marginTop: space.xs },
  switchRow: { flexDirection: "row", alignItems: "center", gap: space.base },

  segments: {
    flexDirection: "row",
    gap: space.xs,
    padding: space.xs,
    borderWidth: 1,
    borderRadius: radius.control + 4,
  },
  segment: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: space.xs + 2,
    paddingVertical: space.md,
    borderRadius: radius.control,
  },

  signOut: { marginTop: space.sm },
});
