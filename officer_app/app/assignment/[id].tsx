import { Feather } from "@expo/vector-icons";
import { useEvent } from "expo";
import { File, Paths } from "expo-file-system";
import * as Haptics from "expo-haptics";
import { Image } from "expo-image";
import * as MediaLibrary from "expo-media-library";
import { useLocalSearchParams, useRouter } from "expo-router";
import * as ScreenOrientation from "expo-screen-orientation";
import { useVideoPlayer, VideoView } from "expo-video";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Animated,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  type TextInputProps,
  useWindowDimensions,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { AutoScrollView } from "@/components/AutoScrollView";
import { getViolationIconName } from "@/constants/violationIcons";
import { Assignment, useAssignments } from "@/context/AssignmentContext";
import { useAuth } from "@/context/AuthContext";
import { useColors } from "@/hooks/useColors";
import * as api from "@/lib/api";

const STATUS_DISPLAY: Record<Assignment["status"], string> = {
  active: "Pending",
  dispatched: "Assigned",
  resolved: "Resolved",
  acknowledged: "Dismissed",
};

const DISMISS_REASONS = [
  "False Alarm",
  "Already Resolved",
  "Duplicate Report",
  "Outside Jurisdiction",
  "Insufficient Evidence",
  "Camera Misdetection",
];

function formatDate(iso: string) {
  return new Date(iso).toLocaleString("en-PH", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function timeSince(iso: string) {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

interface DismissModalProps {
  visible: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => void;
}

function DismissModal({ visible, onClose, onConfirm }: DismissModalProps) {
  const c = useColors();
  const [selectedReason, setSelectedReason] = useState<string | null>(null);
  const [customNote, setCustomNote] = useState("");

  const canSubmit = !!selectedReason || customNote.trim().length > 0;

  const handleConfirm = () => {
    const parts: string[] = [];
    if (selectedReason) parts.push(selectedReason);
    if (customNote.trim()) parts.push(customNote.trim());
    onConfirm(parts.join(" — "));
    setSelectedReason(null);
    setCustomNote("");
  };

  const handleClose = () => {
    setSelectedReason(null);
    setCustomNote("");
    onClose();
  };

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={handleClose}>
      <KeyboardAvoidingView style={styles.modalBackdrop} behavior={Platform.OS === "ios" ? "padding" : "height"}>
        <Pressable style={styles.modalOverlay} onPress={handleClose} />
        <View style={[styles.modalSheet, { backgroundColor: c.card, borderColor: c.border }]}>
          <View style={[styles.modalHandle, { backgroundColor: c.border }]} />

          <View style={styles.modalHeader}>
            <View style={[styles.modalIconWrap, { backgroundColor: c.dangerLight }]}>
              <Feather name="x-circle" size={22} color={c.destructive} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={[styles.modalTitle, { color: c.foreground }]}>Dismiss Assignment</Text>
              <Text style={[styles.modalSubtitle, { color: c.mutedForeground }]}>Select a reason and/or add a description</Text>
            </View>
          </View>

          <Text style={[styles.modalSectionLabel, { color: c.mutedForeground }]}>REASON</Text>
          <View style={styles.reasonGrid}>
            {DISMISS_REASONS.map((reason) => {
              const selected = selectedReason === reason;
              return (
                <Pressable
                  key={reason}
                  onPress={() => {
                    if (Platform.OS !== "web") Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                    setSelectedReason(selected ? null : reason);
                  }}
                  style={({ pressed }) => [
                    styles.reasonChip,
                    {
                      backgroundColor: selected ? c.dangerLight : c.muted,
                      borderColor: selected ? c.destructive : c.border,
                      opacity: pressed ? 0.75 : 1,
                    },
                  ]}
                >
                  {selected && <Feather name="check" size={12} color={c.destructive} />}
                  <Text style={[styles.reasonText, { color: selected ? c.destructive : c.mutedForeground }]}>{reason}</Text>
                </Pressable>
              );
            })}
          </View>

          <Text style={[styles.modalSectionLabel, { color: c.mutedForeground, marginTop: 16 }]}>WHY? (OPTIONAL)</Text>
          <TextInput
            style={[styles.modalInput, { color: c.foreground, borderColor: c.border, backgroundColor: c.muted }]}
            placeholder="Describe why this is being dismissed..."
            placeholderTextColor={c.mutedForeground}
            value={customNote}
            onChangeText={setCustomNote}
            multiline
            numberOfLines={3}
            textAlignVertical="top"
          />

          <View style={styles.modalActions}>
            <Pressable onPress={handleClose} style={({ pressed }) => [styles.modalCancelBtn, { borderColor: c.border, opacity: pressed ? 0.7 : 1 }]}>
              <Text style={[styles.modalCancelText, { color: c.mutedForeground }]}>Cancel</Text>
            </Pressable>
            <Pressable
              onPress={handleConfirm}
              disabled={!canSubmit}
              style={({ pressed }) => [
                styles.modalConfirmBtn,
                { backgroundColor: canSubmit ? c.destructive : c.muted, borderColor: canSubmit ? c.destructive : c.border, opacity: pressed ? 0.8 : 1 },
              ]}
            >
              <Feather name="x" size={16} color={canSubmit ? "#fff" : c.mutedForeground} />
              <Text style={[styles.modalConfirmText, { color: canSubmit ? "#fff" : c.mutedForeground }]}>Dismiss Assignment</Text>
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

// Scope is exactly these four codes — enforced here on the client, not
// assumed from the API response. A stale row (e.g. a leftover curfew
// ViolationType in some environment's DB) must not render as selectable.
const IN_SCOPE_VIOLATION_CODES = ["smoking", "drinking", "parking", "theft"];

const BARANGAY_OPTIONS = [
  { value: "TUGBUNGAN", label: "Tugbungan" },
  { value: "TETUAN", label: "Tetuan" },
  { value: "MERCEDES", label: "Mercedes" },
  { value: "LUNZURAN", label: "Lunzuran" },
  { value: "TUMAGA", label: "Tumaga" },
];

// A picker, not free text — a typo'd suffix is the same class of problem as a
// duplicate violator record (name-matching downstream breaks on it). "None"
// maps to "" so the submitted value is unchanged from what an empty free-text
// field already produced.
const SUFFIX_OPTIONS = ["None", "Jr.", "Sr.", "II", "III", "IV"];

function normalizeName(...parts: string[]): string {
  return parts.join(" ").trim().toLowerCase().replace(/\s+/g, " ");
}

interface UnderlineInputProps extends Omit<TextInputProps, "placeholder" | "placeholderTextColor"> {
  // Omitted (e.g. for Notes, which sits under its own section header
  // already) rather than "" — an empty string would still reserve the
  // label's line height and leave a blank gap above the field.
  label?: string;
  required?: boolean;
}

// Shared by every free-text field in ResolveModal (name parts, notes) so the
// form reads as one style: transparent background, a 1px bottom rule, and —
// the only strong visual weight in the section — a thicker accent-coloured
// rule while focused. `style` (e.g. Notes' taller minHeight) is layered on
// top of, not instead of, that base look.
const UnderlineInput = React.forwardRef<TextInput, UnderlineInputProps>(function UnderlineInput(
  { label, required, value, onChangeText, onFocus, onBlur, style, ...rest },
  ref
) {
  const c = useColors();
  const [focused, setFocused] = useState(false);

  return (
    <View style={cfStyles.fieldGroup}>
      {label && <Text style={[cfStyles.fieldLabel, { color: c.mutedForeground }]}>{label}</Text>}
      <TextInput
        ref={ref}
        style={[
          cfStyles.underlineInput,
          { color: c.foreground, borderBottomColor: focused ? c.accent : c.border, borderBottomWidth: focused ? 2 : 1 },
          style,
        ]}
        placeholder={required ? "Required" : "Optional"}
        placeholderTextColor={c.mutedForeground}
        value={value}
        onChangeText={onChangeText}
        onFocus={(e) => {
          setFocused(true);
          onFocus?.(e);
        }}
        onBlur={(e) => {
          setFocused(false);
          onBlur?.(e);
        }}
        {...rest}
      />
    </View>
  );
});

interface ResolveModalProps {
  visible: boolean;
  assignment: Assignment;
  officerId: number | null;
  officerName: string;
  existingCitations: api.ApiCitation[];
  onClose: () => void;
  onFiled: () => void;
  onFinished: () => void;
}

function ResolveModal({
  visible,
  assignment,
  officerId,
  officerName,
  existingCitations,
  onClose,
  onFiled,
  onFinished,
}: ResolveModalProps) {
  const c = useColors();
  // A definite height (not maxHeight) is required for the ScrollView below
  // to resolve flex: 1 — see the sheet's style comment for why.
  const { height: windowHeight } = useWindowDimensions();

  const [violationTypes, setViolationTypes] = useState<api.ApiViolationType[]>([]);
  const [typesLoading, setTypesLoading] = useState(true);
  const [selectedTypeIds, setSelectedTypeIds] = useState<Set<number>>(new Set());
  const [typesExpanded, setTypesExpanded] = useState(false);

  const [firstName, setFirstName] = useState("");
  const [middleName, setMiddleName] = useState("");
  const [lastName, setLastName] = useState("");
  const [suffix, setSuffix] = useState("");
  const [violatorBarangay, setViolatorBarangay] = useState<string | null>(null);
  const [carriedBarangay, setCarriedBarangay] = useState(false);
  const [notes, setNotes] = useState("");

  const [barangaySheetVisible, setBarangaySheetVisible] = useState(false);
  const [suffixSheetVisible, setSuffixSheetVisible] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [reviewState, setReviewState] = useState<{ action: "finish" | "another"; duplicateName: string | null } | null>(null);
  const [formError, setFormError] = useState("");

  const lastNameRef = useRef<TextInput>(null);
  const scrollRef = useRef<ScrollView>(null);
  // Synchronous guard against a double-tap firing doSubmit twice before
  // React re-renders `submitting` — state updates aren't fast enough to
  // rely on alone for this.
  const submitLockRef = useRef(false);

  // Brief, unmistakable acknowledgment that the previous citation actually
  // filed and this is a fresh one — the persistent "N filed" banner count
  // ticking up is too quiet on its own to read as a state change.
  const [justFiledName, setJustFiledName] = useState<string | null>(null);
  const justFiledAnim = useRef(new Animated.Value(0)).current;

  // Re-triggering interrupts the running sequence (Animated stops the prior
  // one automatically); its callback then fires with finished:false, so it
  // correctly skips clearing the name the new sequence just set.
  const announceJustFiled = (name: string) => {
    setJustFiledName(name);
    justFiledAnim.setValue(0);
    Animated.sequence([
      Animated.timing(justFiledAnim, { toValue: 1, duration: 150, useNativeDriver: true }),
      Animated.delay(1400),
      Animated.timing(justFiledAnim, { toValue: 0, duration: 300, useNativeDriver: true }),
    ]).start(({ finished }) => {
      if (finished) setJustFiledName(null);
    });
  };

  // Fresh form + freshly-fetched, scope-filtered violation types every time
  // the modal opens (not on every render — a mid-session "add another" reset
  // is handled separately, inside doSubmit, so it doesn't refetch).
  useEffect(() => {
    if (!visible) return;
    setTypesLoading(true);
    api
      .getViolationTypes()
      .then((res) => {
        const all = Array.isArray(res) ? res : res.results;
        const inScope = all.filter((t) => IN_SCOPE_VIOLATION_CODES.includes(t.code));
        setViolationTypes(inScope);
        const detected = inScope.find((t) => t.code === assignment.violationType.code);
        setSelectedTypeIds(detected ? new Set([detected.id]) : new Set());
        // Nothing to confirm-and-collapse if the alert's type isn't one we
        // could pre-check — go straight to the full picker.
        setTypesExpanded(!detected);
      })
      .catch(() => setViolationTypes([]))
      .finally(() => setTypesLoading(false));

    setFirstName("");
    setMiddleName("");
    setLastName("");
    setSuffix("");
    setViolatorBarangay(null);
    setCarriedBarangay(false);
    setNotes("");
    setFormError("");
    setJustFiledName(null);
  }, [visible, assignment.violationType.code]);

  const toggleType = (id: number) =>
    setSelectedTypeIds((prev) => {
      const n = new Set(prev);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  const canSubmit =
    firstName.trim().length > 0 &&
    lastName.trim().length > 0 &&
    !!violatorBarangay &&
    selectedTypeIds.size > 0 &&
    !submitting;

  const findDuplicate = () => {
    const entered = normalizeName(firstName, lastName);
    return existingCitations.find((ec) => normalizeName(ec.first_name_entered, ec.last_name_entered) === entered);
  };

  const doSubmit = async (action: "finish" | "another") => {
    if (!canSubmit || officerId == null) return;
    if (submitLockRef.current) return;
    submitLockRef.current = true;
    setSubmitting(true);
    setFormError("");
    try {
      await api.createCitation({
        alert: assignment.dbId,
        officer: officerId,
        first_name_entered: firstName.trim(),
        middle_name_entered: middleName.trim(),
        last_name_entered: lastName.trim(),
        suffix_entered: suffix,
        barangay_of_violation: "TETUAN",
        violator_barangay: violatorBarangay!,
        violations: [...selectedTypeIds],
        notes: notes.trim(),
        resolve_alert: false,
      });
      onFiled();

      if (action === "finish") {
        onFinished();
      } else {
        const filedName = [firstName.trim(), lastName.trim()].filter(Boolean).join(" ");
        // Carry forward violation types + barangay; name and notes are
        // per-person and always cleared. Cursor goes straight to last name.
        setFirstName("");
        setMiddleName("");
        setLastName("");
        setSuffix("");
        setCarriedBarangay(true);
        setNotes("");
        // Carried types are already "confirmed" — re-collapse rather than
        // leave the full picker open for the next person.
        setTypesExpanded(false);
        scrollRef.current?.scrollTo({ y: 0, animated: true });
        if (Platform.OS !== "web") Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        announceJustFiled(filedName);
        requestAnimationFrame(() => lastNameRef.current?.focus());
      }
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save citation.");
    } finally {
      setSubmitting(false);
      submitLockRef.current = false;
    }
  };

  const handlePress = (action: "finish" | "another") => {
    if (!canSubmit) return;
    if (Platform.OS !== "web") Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    const dup = findDuplicate();
    setReviewState({ action, duplicateName: dup?.violator_name ?? null });
  };

  const confirmReview = () => {
    if (!reviewState) return;
    const { action } = reviewState;
    setReviewState(null);
    doSubmit(action);
  };

  const hasFiledAny = existingCitations.length > 0;
  const selectedBarangayLabel = BARANGAY_OPTIONS.find((b) => b.value === violatorBarangay)?.label;

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <KeyboardAvoidingView style={rStyles.backdrop} behavior={Platform.OS === "ios" ? "padding" : "height"}>
        <Pressable style={rStyles.overlay} onPress={onClose} />
        <View style={[rStyles.sheet, { backgroundColor: c.card, borderColor: c.border, height: windowHeight * 0.92 }]}>
          <View style={[rStyles.handle, { backgroundColor: c.border }]} />

          <View style={rStyles.header}>
            <View style={[rStyles.iconWrap, { backgroundColor: "rgba(16,185,129,0.12)" }]}>
              <Feather name="file-text" size={20} color="#10b981" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={[rStyles.title, { color: c.foreground }]}>Confirm Resolution</Text>
              <Text style={[rStyles.subtitle, { color: c.mutedForeground }]}>File a citation for this scene</Text>
            </View>
            <Pressable
              onPress={onClose}
              style={[rStyles.closeBtn, { backgroundColor: c.secondary }]}
              accessibilityLabel="Close"
              accessibilityRole="button"
            >
              <Feather name="x" size={16} color={c.mutedForeground} />
            </Pressable>
          </View>

          <ScrollView
            ref={scrollRef}
            keyboardShouldPersistTaps="handled"
            showsVerticalScrollIndicator={false}
            style={{ flex: 1 }}
            contentContainerStyle={{ paddingTop: 6, paddingBottom: 20 }}
          >
            {justFiledName && (
              <Animated.View
                style={[
                  cfStyles.justFiledBanner,
                  { backgroundColor: c.success, opacity: justFiledAnim, transform: [{ scale: justFiledAnim.interpolate({ inputRange: [0, 1], outputRange: [0.96, 1] }) }] },
                ]}
              >
                <Feather name="check-circle" size={15} color="#fff" />
                <Text style={cfStyles.justFiledBannerText}>
                  Citation filed for {justFiledName} — ready for the next person
                </Text>
              </Animated.View>
            )}

            {hasFiledAny && (
              <View style={[cfStyles.filedBanner, { backgroundColor: c.successLight, borderColor: c.success }]}>
                <Feather name="check-circle" size={13} color={c.success} />
                <Text style={[cfStyles.filedBannerText, { color: c.success }]}>
                  {existingCitations.length} citation{existingCitations.length !== 1 ? "s" : ""} filed so far for this scene
                </Text>
              </View>
            )}

            <Text style={[cfStyles.lockedLine, { color: c.mutedForeground }]}>
              {officerName || "Officer"} · Tetuan (violation site) · {formatDate(assignment.dispatchedAt)}
            </Text>

            <Text style={[cfStyles.sectionLabel, { color: c.mutedForeground }]}>VIOLATION TYPE(S)</Text>
            {typesLoading ? (
              <ActivityIndicator color={c.primary} style={{ marginVertical: 12 }} accessibilityLabel="Loading violation types" />
            ) : !typesExpanded ? (
              <View>
                {violationTypes
                  .filter((t) => selectedTypeIds.has(t.id))
                  .map((t) => (
                    <View key={t.id} style={[cfStyles.confirmedTypeRow, { backgroundColor: `${t.color}18`, borderColor: t.color }]}>
                      <Feather name={getViolationIconName(t.icon)} size={16} color={t.color} />
                      <Text style={[cfStyles.typeLabel, { color: t.color }]}>{t.label}</Text>
                      <Feather name="check" size={15} color={t.color} style={{ marginLeft: "auto" }} />
                    </View>
                  ))}
                <Pressable
                  onPress={() => setTypesExpanded(true)}
                  accessibilityRole="button"
                  accessibilityLabel="Add another violation type"
                  style={cfStyles.addAnotherLink}
                >
                  <Feather name="plus" size={13} color={c.info} />
                  <Text style={[cfStyles.addAnotherLinkText, { color: c.info }]}>Add another violation</Text>
                </Pressable>
              </View>
            ) : (
              <View style={cfStyles.typeGrid}>
                {violationTypes.map((t) => {
                  const checked = selectedTypeIds.has(t.id);
                  return (
                    <Pressable
                      key={t.id}
                      onPress={() => toggleType(t.id)}
                      accessibilityRole="checkbox"
                      accessibilityState={{ checked }}
                      accessibilityLabel={t.label}
                      style={({ pressed }) => [
                        cfStyles.typeRow,
                        {
                          backgroundColor: checked ? `${t.color}18` : c.secondary,
                          borderColor: checked ? t.color : c.border,
                          opacity: pressed ? 0.8 : 1,
                        },
                      ]}
                    >
                      <Feather name={getViolationIconName(t.icon)} size={18} color={checked ? t.color : c.mutedForeground} />
                      <Text style={[cfStyles.typeLabel, { color: checked ? t.color : c.foreground }]}>{t.label}</Text>
                      {checked && <Feather name="check" size={16} color={t.color} style={{ marginLeft: "auto" }} />}
                    </Pressable>
                  );
                })}
              </View>
            )}

            <Text style={[cfStyles.sectionLabel, { color: c.mutedForeground, marginTop: 18 }]}>VIOLATOR NAME</Text>
            <UnderlineInput label="First name" required value={firstName} onChangeText={setFirstName} />
            <UnderlineInput label="Middle name" value={middleName} onChangeText={setMiddleName} />
            <UnderlineInput label="Last name" required ref={lastNameRef} value={lastName} onChangeText={setLastName} />
            <View style={cfStyles.fieldGroup}>
              <Text style={[cfStyles.fieldLabel, { color: c.mutedForeground }]}>Suffix</Text>
              <Pressable
                onPress={() => setSuffixSheetVisible(true)}
                style={[cfStyles.underlineInput, cfStyles.underlinePicker, { borderBottomColor: c.border }]}
                accessibilityRole="button"
                accessibilityLabel="Select suffix"
              >
                <Text style={[cfStyles.underlinePickerText, { color: suffix ? c.foreground : c.mutedForeground }]}>
                  {suffix || "None"}
                </Text>
                <Feather name="chevron-down" size={16} color={c.mutedForeground} />
              </Pressable>
            </View>

            <Text style={[cfStyles.sectionLabel, { color: c.mutedForeground, marginTop: 18 }]}>VIOLATOR&apos;S HOME BARANGAY</Text>
            <Pressable
              onPress={() => setBarangaySheetVisible(true)}
              style={[cfStyles.underlineInput, cfStyles.underlinePicker, { borderBottomColor: c.border }]}
              accessibilityRole="button"
              accessibilityLabel="Select violator's home barangay"
            >
              <Feather name="map-pin" size={15} color={c.mutedForeground} />
              <Text style={[cfStyles.underlinePickerText, { color: violatorBarangay ? c.foreground : c.mutedForeground }]}>
                {selectedBarangayLabel ?? "Select barangay…"}
              </Text>
              {carriedBarangay && violatorBarangay && (
                <View style={[cfStyles.sameAsBadge, { backgroundColor: c.infoLight }]}>
                  <Text style={[cfStyles.sameAsBadgeText, { color: c.info }]}>same as previous</Text>
                </View>
              )}
              <Feather name="chevron-right" size={16} color={c.mutedForeground} />
            </Pressable>

            <Text style={[cfStyles.sectionLabel, { color: c.mutedForeground, marginTop: 18 }]}>NOTES (OPTIONAL)</Text>
            <TextInput
              style={[cfStyles.notesInput, { color: c.foreground, borderColor: c.border, backgroundColor: c.muted }]}
              placeholder="e.g. two others fled on approach"
              placeholderTextColor={c.mutedForeground}
              value={notes}
              onChangeText={setNotes}
              multiline
              numberOfLines={3}
              textAlignVertical="top"
            />

            {!!formError && <Text style={[cfStyles.errorText, { color: c.destructive }]}>{formError}</Text>}
          </ScrollView>

          <View style={cfStyles.actionRow}>
            <Pressable
              onPress={() => handlePress("another")}
              disabled={!canSubmit}
              accessibilityRole="button"
              accessibilityLabel="Save and add more citations"
              style={[
                cfStyles.secondaryBtn,
                hasFiledAny
                  ? { backgroundColor: c.info, borderColor: c.info }
                  : { backgroundColor: "transparent", borderColor: c.border },
                { opacity: !canSubmit ? 0.5 : 1 },
              ]}
            >
              <Feather name="user-plus" size={15} color={hasFiledAny ? "#fff" : c.foreground} />
              <Text style={[cfStyles.secondaryBtnText, { color: hasFiledAny ? "#fff" : c.foreground }]}>Save & add more</Text>
            </Pressable>
            <Pressable
              onPress={() => handlePress("finish")}
              disabled={!canSubmit}
              accessibilityRole="button"
              accessibilityLabel="Save and finish, resolving this assignment"
              style={[cfStyles.primaryBtn, { backgroundColor: "#10b981", opacity: !canSubmit ? 0.5 : 1 }]}
            >
              {submitting ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <>
                  <Feather name="check-circle" size={16} color="#fff" />
                  <Text style={cfStyles.primaryBtnText}>Save & finish</Text>
                </>
              )}
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>

      <Modal visible={barangaySheetVisible} animationType="slide" transparent onRequestClose={() => setBarangaySheetVisible(false)}>
        <Pressable style={rStyles.overlay} onPress={() => setBarangaySheetVisible(false)} />
        <View style={[cfStyles.barangaySheet, { backgroundColor: c.card, borderColor: c.border }]}>
          <View style={[rStyles.handle, { backgroundColor: c.border }]} />
          <Text style={[rStyles.title, { color: c.foreground, marginBottom: 10 }]}>Home barangay</Text>
          {BARANGAY_OPTIONS.map((b) => (
            <Pressable
              key={b.value}
              onPress={() => {
                setViolatorBarangay(b.value);
                setCarriedBarangay(false);
                setBarangaySheetVisible(false);
              }}
              accessibilityRole="button"
              accessibilityState={{ selected: violatorBarangay === b.value }}
              style={[
                cfStyles.barangayRow,
                { backgroundColor: violatorBarangay === b.value ? c.successLight : c.secondary, borderColor: c.border },
              ]}
            >
              <Text style={[cfStyles.barangayRowText, { color: c.foreground }]}>{b.label}</Text>
              {violatorBarangay === b.value && <Feather name="check" size={16} color={c.success} />}
            </Pressable>
          ))}
        </View>
      </Modal>

      <Modal visible={suffixSheetVisible} animationType="slide" transparent onRequestClose={() => setSuffixSheetVisible(false)}>
        <Pressable style={rStyles.overlay} onPress={() => setSuffixSheetVisible(false)} />
        <View style={[cfStyles.barangaySheet, { backgroundColor: c.card, borderColor: c.border }]}>
          <View style={[rStyles.handle, { backgroundColor: c.border }]} />
          <Text style={[rStyles.title, { color: c.foreground, marginBottom: 10 }]}>Suffix</Text>
          {SUFFIX_OPTIONS.map((opt) => {
            // "None" is the picker's face for the empty string the field
            // already defaults to and submits — not a fifth real value.
            const optValue = opt === "None" ? "" : opt;
            const selected = suffix === optValue;
            return (
              <Pressable
                key={opt}
                onPress={() => {
                  setSuffix(optValue);
                  setSuffixSheetVisible(false);
                }}
                accessibilityRole="button"
                accessibilityState={{ selected }}
                style={[cfStyles.barangayRow, { backgroundColor: selected ? c.successLight : c.secondary, borderColor: c.border }]}
              >
                <Text style={[cfStyles.barangayRowText, { color: c.foreground }]}>{opt}</Text>
                {selected && <Feather name="check" size={16} color={c.success} />}
              </Pressable>
            );
          })}
        </View>
      </Modal>

      <Modal visible={!!reviewState} animationType="fade" transparent onRequestClose={() => setReviewState(null)}>
        <Pressable style={officersStyles.overlay} onPress={() => setReviewState(null)}>
          <Pressable
            style={[officersStyles.sheet, { backgroundColor: c.card, borderColor: c.border }]}
            onPress={(e) => e.stopPropagation()}
          >
            <View style={rStyles.header}>
              <View style={[rStyles.iconWrap, { backgroundColor: c.successLight }]}>
                <Feather name="clipboard" size={18} color={c.success} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={[rStyles.title, { color: c.foreground }]}>Review citation</Text>
                <Text style={[rStyles.subtitle, { color: c.mutedForeground }]}>
                  {reviewState?.action === "finish" ? "This will resolve the assignment." : "You can file another after this."}
                </Text>
              </View>
            </View>

            {reviewState?.duplicateName && (
              <View style={[cfStyles.filedBanner, { backgroundColor: c.warningLight, borderColor: c.warning, marginTop: 6 }]}>
                <Feather name="alert-triangle" size={13} color={c.warning} />
                <Text style={[cfStyles.filedBannerText, { color: c.warning }]}>
                  Already cited a {reviewState.duplicateName} for this alert.
                </Text>
              </View>
            )}

            <View style={cfStyles.reviewBlock}>
              <Text style={[cfStyles.reviewLabel, { color: c.mutedForeground }]}>VIOLATOR</Text>
              <Text style={[cfStyles.reviewValue, { color: c.foreground }]}>
                {[firstName, middleName, lastName, suffix].filter(Boolean).join(" ")}
              </Text>
            </View>
            <View style={cfStyles.reviewBlock}>
              <Text style={[cfStyles.reviewLabel, { color: c.mutedForeground }]}>VIOLATION TYPE(S)</Text>
              <Text style={[cfStyles.reviewValue, { color: c.foreground }]}>
                {violationTypes
                  .filter((t) => selectedTypeIds.has(t.id))
                  .map((t) => t.label)
                  .join(", ")}
              </Text>
            </View>
            <View style={cfStyles.reviewBlock}>
              <Text style={[cfStyles.reviewLabel, { color: c.mutedForeground }]}>HOME BARANGAY</Text>
              <Text style={[cfStyles.reviewValue, { color: c.foreground }]}>{selectedBarangayLabel}</Text>
            </View>

            <View style={[rStyles.footerBtns, { marginTop: 16, width: "100%" }]}>
              <Pressable onPress={() => setReviewState(null)} style={[rStyles.cancelBtn, { borderColor: c.border }]}>
                <Text style={[rStyles.cancelText, { color: c.mutedForeground }]}>Cancel</Text>
              </Pressable>
              <Pressable onPress={confirmReview} style={rStyles.confirmBtn}>
                <Feather name="check-circle" size={16} color="#fff" />
                <Text style={rStyles.confirmText}>Confirm</Text>
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>
    </Modal>
  );
}

function RecordingPlayer({
  imageUrl,
  videoUrl,
  code,
  camera,
  zone,
  timestamp,
}: {
  imageUrl: string;
  videoUrl: string;
  code: string;
  camera: string | null;
  zone: string;
  timestamp: string;
}) {
  const c = useColors();
  const hasVideo = !!videoUrl;
  const [fullscreen, setFullscreen] = useState(false);
  const [fetchFailed, setFetchFailed] = useState(false);
  const [frameRendered, setFrameRendered] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [trackWidth, setTrackWidth] = useState(0);

  // Landscape while fullscreen — a 16:9 clip in a portrait-locked box has no
  // room to breathe, and "contain" would just add huge letterbox bars without
  // this. The single effect (rather than one for enter + one for unmount)
  // means React runs the SAME cleanup — unlockAsync — whether fullscreen
  // toggles off normally or the screen unmounts mid-fullscreen (e.g. the
  // officer backs out), so the app is never left stuck sideways either way.
  useEffect(() => {
    if (fullscreen) {
      ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.LANDSCAPE);
      return () => {
        ScreenOrientation.unlockAsync();
      };
    }
    return undefined;
  }, [fullscreen]);

  // null when there's no clip at all, or once a fetch has failed. Flipping
  // fetchFailed back to false on retry hands useVideoPlayer a fresh source
  // identity, so it builds a brand-new player instead of reusing a dead one.
  const source = hasVideo && !fetchFailed ? videoUrl : null;
  const player = useVideoPlayer(source, (p) => {
    p.loop = false;
    p.timeUpdateEventInterval = 0.25;
  });

  const { status } = useEvent(player, "statusChange", { status: player.status });
  const { isPlaying } = useEvent(player, "playingChange", { isPlaying: player.playing });
  const { currentTime } = useEvent(player, "timeUpdate", {
    currentTime: player.currentTime,
    currentLiveTimestamp: null,
    currentOffsetFromLive: null,
    bufferedPosition: player.bufferedPosition,
  });
  // sourceLoad fires once metadata has finished loading, independent of
  // timeUpdate (which only ticks during actual playback) — without this,
  // player.duration is populated internally but nothing re-renders this
  // component to show it, so the officer would see 0:00 / 0:00 until play.
  const { duration: loadedDuration } = useEvent(player, "sourceLoad", {
    videoSource: null, duration: 0,
    availableVideoTracks: [], availableSubtitleTracks: [], availableAudioTracks: [],
  });

  useEffect(() => {
    if (status === "error") setFetchFailed(true);
  }, [status]);

  useEffect(() => {
    setFrameRendered(false);
  }, [source]);

  const duration = player.duration || loadedDuration || 0;
  const pct = duration > 0 ? Math.min(100, (currentTime / duration) * 100) : 0;
  const fmtSec = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const togglePlay = () => (player.playing ? player.pause() : player.play());
  const skip = (delta: number) => player.seekBy(delta);
  const seekToRatio = (ratio: number) => {
    if (duration > 0) player.currentTime = Math.min(duration, Math.max(0, ratio * duration));
  };
  const retry = () => setFetchFailed(false);

  const handleSaveClip = async () => {
    if (!hasVideo || saving) return;
    setSaving(true);
    setSaveError("");
    setSaveSuccess(false);
    try {
      // Write-only permission (true) — saving evidence into the gallery never
      // needs to read the officer's existing photos.
      const { granted, canAskAgain } = await MediaLibrary.requestPermissionsAsync(true);
      if (!granted) {
        setSaveError(
          canAskAgain
            ? "Photo library permission is needed to save the clip."
            : "Photo library access is denied. Enable it in Settings to save clips."
        );
        return;
      }

      // Evidence-grade filename: the alert code, not whatever the URL's path
      // happens to end in. Code is already filesystem-safe (ALT-#### —
      // alphanumeric + hyphen), but strip anything else just in case.
      const safeCode = (code || "clip").replace(/[^a-zA-Z0-9_-]/g, "_");
      const destFile = new File(Paths.cache, `${safeCode}.mp4`);
      // Belt-and-suspenders against "destination exists": delete any leftover
      // from a previous save ourselves rather than relying solely on
      // `idempotent` to overwrite cleanly on every platform.
      if (destFile.exists) destFile.delete();
      const downloaded = await File.downloadFileAsync(videoUrl, destFile, { idempotent: true });
      await MediaLibrary.saveToLibraryAsync(downloaded.uri);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Could not save the clip.");
    } finally {
      setSaving(false);
    }
  };

  // Stable source object so expo-image never treats a parent re-render as a
  // source change (which would reload the image and flicker).
  const imageSource = useMemo(() => ({ uri: imageUrl }), [imageUrl]);

  const showLoading = hasVideo && !fetchFailed && (status === "idle" || status === "loading") && !frameRendered;
  const showUnavailable = !hasVideo || fetchFailed;
  const showPlayer = hasVideo && !fetchFailed;

  const content = (
    <>
      <View style={[rpStyles.imageBox, fullscreen && rpStyles.imageBoxFullscreen]}>
        <Image source={imageSource} style={[rpStyles.image, { opacity: frameRendered ? 0 : 1 }]} contentFit="cover" />
        {showPlayer && (
          <VideoView
            player={player}
            style={StyleSheet.absoluteFill}
            nativeControls={false}
            contentFit={fullscreen ? "contain" : "cover"}
            onFirstFrameRender={() => setFrameRendered(true)}
          />
        )}
        {showLoading && (
          <View style={rpStyles.centerOverlay}>
            <ActivityIndicator color="#fff" />
          </View>
        )}
        {showUnavailable && (
          <View style={rpStyles.centerOverlay}>
            <Feather name="video-off" size={18} color="rgba(255,255,255,0.85)" />
            <Text style={rpStyles.unavailableText}>Video unavailable</Text>
            {hasVideo && (
              <Pressable onPress={retry} style={rpStyles.retryBtn} hitSlop={8}>
                <Feather name="refresh-cw" size={12} color="#fff" />
                <Text style={rpStyles.retryText}>Retry</Text>
              </Pressable>
            )}
          </View>
        )}
        <View style={rpStyles.topOverlay}>
          <View style={rpStyles.recRow}>
            <View style={rpStyles.recBadge}>
              <Text style={rpStyles.recText}>● REC</Text>
            </View>
            <Text style={rpStyles.overlayMono}>{camera ?? "—"}</Text>
          </View>
          <Text style={rpStyles.overlayMono}>{formatDate(timestamp)}</Text>
        </View>
        {!!zone && <Text style={rpStyles.zoneLabel}>{zone}</Text>}
      </View>
      <View style={[rpStyles.controls, { backgroundColor: c.card }]}>
        <Pressable
          onLayout={(e) => setTrackWidth(e.nativeEvent.layout.width)}
          onPress={(e) => { if (showPlayer && trackWidth > 0) seekToRatio(e.nativeEvent.locationX / trackWidth); }}
          style={[rpStyles.progressTrack, { backgroundColor: c.border }]}
        >
          <View style={[rpStyles.progressFill, { width: `${pct}%`, backgroundColor: c.primary }]} />
        </Pressable>
        <View style={rpStyles.controlsRow}>
          <View style={rpStyles.controlsLeft}>
            <Pressable onPress={() => skip(-5)} hitSlop={8} disabled={!showPlayer}>
              <Feather name="rotate-ccw" size={15} color={c.mutedForeground} />
            </Pressable>
            <Pressable onPress={togglePlay} disabled={!showPlayer}
              style={[rpStyles.playPauseBtn, { backgroundColor: showPlayer ? c.primary : c.border }]}>
              <Feather name={isPlaying ? "pause" : "play"} size={13} color="#0c0f16" style={isPlaying ? undefined : { marginLeft: 1.5 }} />
            </Pressable>
            <Pressable onPress={() => skip(5)} hitSlop={8} disabled={!showPlayer}>
              <Feather name="rotate-cw" size={15} color={c.mutedForeground} />
            </Pressable>
            <Text style={[rpStyles.timeText, { color: c.mutedForeground }]}>{fmtSec(currentTime)} / {fmtSec(duration)}</Text>
          </View>
          <View style={rpStyles.controlsRight}>
            <Pressable onPress={() => setFullscreen((f) => !f)} hitSlop={8}>
              <Feather name={fullscreen ? "minimize" : "maximize"} size={14} color={c.mutedForeground} />
            </Pressable>
            <Pressable onPress={handleSaveClip} disabled={!hasVideo || saving} style={rpStyles.saveClipBtn} hitSlop={8}>
              {saving
                ? <ActivityIndicator size="small" color={c.mutedForeground} />
                : <Feather name="download" size={11} color={hasVideo ? c.mutedForeground : c.border} />}
              <Text style={[rpStyles.saveClipText, { color: hasVideo ? c.mutedForeground : c.border }]}>Save clip</Text>
            </Pressable>
          </View>
        </View>
        {!!saveError && <Text style={rpStyles.saveErrorText}>{saveError}</Text>}
        {saveSuccess && <Text style={rpStyles.saveSuccessText}>Saved to gallery</Text>}
      </View>
    </>
  );

  if (fullscreen) {
    return (
      <Modal visible transparent animationType="fade" onRequestClose={() => setFullscreen(false)}>
        <View style={rpStyles.fullscreenBackdrop}>
          <View style={rpStyles.fullscreenWrap}>{content}</View>
        </View>
      </Modal>
    );
  }

  return <View style={rpStyles.wrap}>{content}</View>;
}

const rpStyles = StyleSheet.create({
  wrap: { borderRadius: 18, overflow: "hidden" },
  imageBox: { width: "100%", aspectRatio: 16 / 9, backgroundColor: "#000", position: "relative" },
  imageBoxFullscreen: { aspectRatio: undefined, flex: 1 },
  image: { width: "100%", height: "100%" },
  topOverlay: { position: "absolute", top: 0, left: 0, right: 0, flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 10, paddingVertical: 8 },
  recRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  recBadge: { paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: "rgba(239,68,68,0.85)" },
  recText: { color: "#fff", fontSize: 9, fontFamily: "Inter_600SemiBold" },
  overlayMono: { color: "rgba(255,255,255,0.8)", fontSize: 10, fontFamily: "Inter_500Medium" },
  zoneLabel: { position: "absolute", bottom: 34, left: 10, color: "rgba(255,255,255,0.8)", fontSize: 10, fontFamily: "Inter_500Medium" },
  controls: { paddingHorizontal: 14, paddingVertical: 10, gap: 8 },
  progressTrack: { height: 4, borderRadius: 2, position: "relative" },
  progressFill: { position: "absolute", left: 0, top: 0, height: "100%", borderRadius: 2 },
  centerOverlay: { ...StyleSheet.absoluteFillObject, alignItems: "center", justifyContent: "center", gap: 6, backgroundColor: "rgba(0,0,0,0.25)" },
  unavailableText: { color: "rgba(255,255,255,0.85)", fontSize: 11, fontFamily: "Inter_500Medium" },
  retryBtn: { flexDirection: "row", alignItems: "center", gap: 4, marginTop: 4, paddingHorizontal: 8, paddingVertical: 4, borderRadius: 12, backgroundColor: "rgba(255,255,255,0.15)" },
  retryText: { color: "#fff", fontSize: 11, fontFamily: "Inter_600SemiBold" },
  saveErrorText: { color: "#ef4444", fontSize: 10, fontFamily: "Inter_500Medium" },
  saveSuccessText: { color: "#10b981", fontSize: 10, fontFamily: "Inter_500Medium" },
  controlsRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  controlsLeft: { flexDirection: "row", alignItems: "center", gap: 12 },
  controlsRight: { flexDirection: "row", alignItems: "center", gap: 14 },
  playPauseBtn: { width: 28, height: 28, borderRadius: 14, alignItems: "center", justifyContent: "center" },
  timeText: { fontSize: 11, fontFamily: "Inter_500Medium" },
  saveClipBtn: { flexDirection: "row", alignItems: "center", gap: 4 },
  saveClipText: { fontSize: 11, fontFamily: "Inter_500Medium" },
  fullscreenBackdrop: { flex: 1, backgroundColor: "#000", justifyContent: "center" },
  // Explicit black here too (not just the backdrop) so nothing between the
  // video's own letterbox and the Modal's edge can show var(--card)/white
  // through a gap — e.g. safe-area insets in landscape that imageBox alone
  // wouldn't cover.
  fullscreenWrap: { flex: 1, backgroundColor: "#000" },
});

function NoiseViolationCard({
  camera,
  confidence,
}: {
  camera: string | null;
  confidence: number;
}) {
  const c = useColors();
  const loudnessPct = Math.round(confidence * 100);
  const dBFS = Math.round(-30 + confidence * 30);
  const accentColor = "#f59e0b";

  return (
    <View style={[nvStyles.card, { backgroundColor: c.secondary, borderColor: c.border, borderLeftColor: accentColor }]}>
      <Text style={[nvStyles.typeLabel, { color: c.mutedForeground }]}>
        Noise violation — no facial recognition, loudness only
      </Text>

      {/* Source + Duration */}
      <View style={[nvStyles.topRow, { borderBottomColor: c.border }]}>
        <View style={nvStyles.topCell}>
          <Text style={[nvStyles.topLabel, { color: c.mutedForeground }]}>Source</Text>
          <Text style={[nvStyles.topValue, { color: c.foreground }]}>{camera ?? "—"} · mic</Text>
        </View>
        <View style={[nvStyles.vDivider, { backgroundColor: c.border }]} />
        <View style={nvStyles.topCell}>
          <Text style={[nvStyles.topLabel, { color: c.mutedForeground }]}>Duration above threshold</Text>
          <Text style={[nvStyles.topValue, { color: accentColor }]}>— s</Text>
        </View>
      </View>

      {/* Loudness */}
      <View style={nvStyles.loudnessSection}>
        <View style={nvStyles.loudnessHeader}>
          <Text style={[nvStyles.loudnessLabel, { color: c.mutedForeground }]}>Relative loudness</Text>
        </View>

        {/* Bar */}
        <View style={[nvStyles.barTrack, { backgroundColor: c.muted }]}>
          <View style={[nvStyles.barFill, { width: `${loudnessPct}%` as any, backgroundColor: accentColor }]} />
          <View style={[nvStyles.thresholdLine, { backgroundColor: c.foreground }]} />
        </View>
        <View style={nvStyles.barLabels}>
          <Text style={[nvStyles.thresholdLabel, { color: c.mutedForeground }]}>threshold</Text>
          <Text style={[nvStyles.dBFS, { color: accentColor }]}>{dBFS} dBFS</Text>
        </View>
      </View>
    </View>
  );
}

const nvStyles = StyleSheet.create({
  card: { borderRadius: 12, borderWidth: 1, borderLeftWidth: 3, overflow: "hidden" },
  typeLabel: { fontSize: 10, fontFamily: "Inter_400Regular", paddingHorizontal: 14, paddingTop: 10, paddingBottom: 6 },
  topRow: { flexDirection: "row", borderBottomWidth: 1, paddingHorizontal: 14, paddingBottom: 12 },
  topCell: { flex: 1 },
  topLabel: { fontSize: 10, fontFamily: "Inter_400Regular", marginBottom: 3 },
  topValue: { fontSize: 14, fontFamily: "Inter_700Bold" },
  vDivider: { width: 1, marginHorizontal: 12, marginVertical: 2 },
  loudnessSection: { padding: 14 },
  loudnessHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 10 },
  loudnessLabel: { fontSize: 10, fontFamily: "Inter_400Regular" },
  badge: { paddingHorizontal: 8, paddingVertical: 4, borderRadius: 20 },
  badgeText: { fontSize: 10, fontFamily: "Inter_600SemiBold" },
  barTrack: { height: 8, borderRadius: 4, overflow: "visible", position: "relative" },
  barFill: { position: "absolute", left: 0, top: 0, height: "100%", borderRadius: 4 },
  thresholdLine: { position: "absolute", left: "75%", top: -4, width: 2, height: 16, borderRadius: 1 },
  barLabels: { flexDirection: "row", justifyContent: "space-between", marginTop: 6 },
  thresholdLabel: { fontSize: 9, fontFamily: "Inter_400Regular", marginLeft: "55%" as any },
  dBFS: { fontSize: 10, fontFamily: "Inter_600SemiBold" },
  actionRow: { flexDirection: "row", gap: 10, marginTop: 12 },
  notBtn: { flex: 1, paddingVertical: 12, borderRadius: 10, borderWidth: 1, alignItems: "center", justifyContent: "center" },
  notBtnText: { fontSize: 14, fontFamily: "Inter_600SemiBold" },
  confirmBtn: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 12, borderRadius: 10, backgroundColor: "#f59e0b" },
  confirmBtnText: { color: "#fff", fontSize: 14, fontFamily: "Inter_600SemiBold" },
});

export default function AssignmentDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { getAssignment, acceptAssignment, resolveAssignment, dismissAssignment } = useAssignments();
  const { officer } = useAuth();
  const c = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const assignment = getAssignment(id ?? "");
  const [dismissModalVisible, setDismissModalVisible] = useState(false);
  const [resolveModalVisible, setResolveModalVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showAllOfficers, setShowAllOfficers] = useState(false);
  const [citationsForAlert, setCitationsForAlert] = useState<api.ApiCitation[]>([]);

  const isNoiseViolation = assignment?.violationType.code === "noise";

  // Sourced from the server (not local state) so a partially-filed scene —
  // two of four cited, app closed and reopened — still shows "2 filed"
  // instead of resetting to zero.
  const refreshCitations = useCallback(async () => {
    if (!assignment) return;
    try {
      const res = await api.getCitations({ alert: String(assignment.dbId) });
      setCitationsForAlert(Array.isArray(res) ? res : res.results);
    } catch {
      // Non-fatal — the filed-count is a convenience, not required to act on the assignment.
    }
  }, [assignment?.dbId]);

  useEffect(() => {
    refreshCitations();
  }, [refreshCitations]);

  if (!assignment) {
    return (
      <View style={[styles.root, { backgroundColor: c.background, justifyContent: "center", alignItems: "center" }]}>
        <Feather name="alert-circle" size={40} color={c.mutedForeground} />
        <Text style={[styles.notFound, { color: c.mutedForeground }]}>Assignment not found</Text>
      </View>
    );
  }

  const isUnassigned = assignment.status === "active";
  const isMine = officer?.officerId != null && assignment.assignedOfficerIds.includes(officer.officerId);
  const canAct = (isUnassigned || isMine) && assignment.status !== "resolved" && assignment.status !== "acknowledged";
  const isClosed = assignment.status === "resolved" || assignment.status === "acknowledged";

  const statusColor =
    assignment.status === "active" ? c.danger : isClosed ? c.mutedForeground : c.primary;

  const handleAccept = async () => {
    if (!officer) return;
    setBusy(true);
    if (Platform.OS !== "web") Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    try {
      await acceptAssignment(assignment.id);
    } finally {
      setBusy(false);
    }
  };

  const handleFinished = async () => {
    setBusy(true);
    try {
      await resolveAssignment(assignment.id);
      setResolveModalVisible(false);
      router.back();
    } finally {
      setBusy(false);
    }
  };

  const handleDismissConfirm = async (reason: string) => {
    if (Platform.OS !== "web") Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
    setBusy(true);
    try {
      await dismissAssignment(assignment.id, reason);
      setDismissModalVisible(false);
      router.back();
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={[styles.root, { backgroundColor: c.background }]}>
      <View
        style={[
          styles.topBar,
          { backgroundColor: c.card, borderBottomColor: c.border, paddingTop: insets.top + (Platform.OS === "web" ? 20 : 10) },
        ]}
      >
        <Pressable onPress={() => router.back()} style={styles.backBtn}>
          <Feather name="arrow-left" size={22} color={c.foreground} />
        </Pressable>
        <Text style={[styles.topBarTitle, { color: c.foreground }]}>Assignment</Text>
        <View style={[styles.statusPillTop, { borderColor: statusColor }]}>
          <Text style={[styles.statusPillText, { color: statusColor }]}>{STATUS_DISPLAY[assignment.status]}</Text>
        </View>
      </View>

      <AutoScrollView
        style={{ backgroundColor: c.card }}
        contentContainerStyle={[styles.scroll, { paddingBottom: insets.bottom + (Platform.OS === "web" ? 34 : 24) }]}
        bounces={false}
        overScrollMode="never"
      >
        <View style={[styles.card, { backgroundColor: c.card, borderColor: c.border }]}>
          <View style={styles.violationHeader}>
            <View style={[styles.iconCircle, { backgroundColor: `${assignment.violationType.color}22` }]}>
              <Feather name={getViolationIconName(assignment.violationType.icon)} size={26} color={assignment.violationType.color} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={[styles.violationType, { color: c.foreground }]}>{assignment.violationType.label}</Text>
              <Text style={[styles.codeText, { color: c.mutedForeground }]}>{assignment.code}</Text>
            </View>
          </View>
        </View>

        {/* Dismissal reason — shown when the assignment was dismissed */}
        {assignment.status === "acknowledged" && !!assignment.notes && (
          <View style={[styles.card, { backgroundColor: c.dangerLight, borderColor: c.destructive }]}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
              <Feather name="x-circle" size={15} color={c.destructive} />
              <Text style={[styles.cardLabel, { color: c.destructive }]}>DISMISSAL REASON</Text>
            </View>
            <Text style={[styles.description, { color: c.foreground, fontSize: 14 }]}>{assignment.notes}</Text>
          </View>
        )}

        {/* Recording — evidence clip captured at the moment of detection */}
        {(!!assignment.imageUrl || !!assignment.videoUrl) && (
          <RecordingPlayer
            imageUrl={assignment.imageUrl}
            videoUrl={assignment.videoUrl}
            code={assignment.code}
            camera={assignment.cameraCode}
            zone={assignment.location}
            timestamp={assignment.dispatchedAt}
          />
        )}

        {!isNoiseViolation && (
          <View style={[styles.card, { backgroundColor: c.card, borderColor: c.border }]}>
            <Text style={[styles.description, { color: c.foreground }]}>{assignment.description}</Text>
          </View>
        )}

        <View style={[styles.card, { backgroundColor: c.card, borderColor: c.border }]}>
          <Text style={[styles.cardLabel, { color: c.mutedForeground }]}>LOCATION</Text>
          {[
            { icon: "map-pin" as const, label: "Zone / Camera", value: assignment.location },
            { icon: "percent" as const, label: "AI Confidence", value: `${assignment.confidence}%` },
          ].map((row) => (
            <View key={row.label} style={[styles.infoRow, { borderBottomColor: c.border }]}>
              <Feather name={row.icon} size={15} color={c.mutedForeground} />
              <View style={{ flex: 1 }}>
                <Text style={[styles.infoLabel, { color: c.mutedForeground }]}>{row.label}</Text>
                <Text style={[styles.infoValue, { color: c.foreground }]}>{row.value}</Text>
              </View>
            </View>
          ))}
        </View>

        <View style={[styles.card, { backgroundColor: c.card, borderColor: c.border }]}>
          <Text style={[styles.cardLabel, { color: c.mutedForeground }]}>ASSIGNMENT INFO</Text>
          {[
            { icon: "clock" as const, label: "Detected", value: `${formatDate(assignment.dispatchedAt)} · ${timeSince(assignment.dispatchedAt)}` },
          ].map((row) => (
            <View key={row.label} style={[styles.infoRow, { borderBottomColor: c.border, borderBottomWidth: 0, paddingBottom: 0 }]}>
              <Feather name={row.icon} size={15} color={c.mutedForeground} />
              <View style={{ flex: 1 }}>
                <Text style={[styles.infoLabel, { color: c.mutedForeground }]}>{row.label}</Text>
                <Text style={[styles.infoValue, { color: c.foreground }]}>{row.value}</Text>
              </View>
            </View>
          ))}
        </View>

        <View style={[styles.card, { backgroundColor: c.card, borderColor: c.border }]}>
          <Text style={[styles.cardLabel, { color: c.mutedForeground }]}>
            ASSIGNED OFFICERS{assignment.assignedOfficerNames.length > 0 ? ` (${assignment.assignedOfficerNames.length})` : ""}
          </Text>
          {assignment.assignedOfficerNames.length === 0 ? (
            <Text style={[styles.infoValue, { color: c.mutedForeground, marginTop: 0 }]}>None assigned</Text>
          ) : (
            <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
              <Feather name="shield" size={14} color={c.success} />
              <Text style={[styles.infoValue, { color: c.foreground, marginTop: 0 }]}>
                {assignment.assignedOfficerNames[0].split(" ")[0]}
              </Text>
              {assignment.assignedOfficerNames.length > 1 && (
                <Pressable onPress={() => setShowAllOfficers(true)}>
                  <Text style={{ color: c.info, fontSize: 13, fontFamily: "Inter_600SemiBold" }}>…more</Text>
                </Pressable>
              )}
            </View>
          )}
        </View>

        {/* Citations filed so far — the officer's working memory for a multi-person scene */}
        {citationsForAlert.length > 0 && (
          <View style={[styles.card, { backgroundColor: c.card, borderColor: c.border }]}>
            <Text style={[styles.cardLabel, { color: c.mutedForeground }]}>
              CITATIONS FILED ({citationsForAlert.length})
            </Text>
            <View style={{ gap: 8 }}>
              {citationsForAlert.map((cit) => (
                <View key={cit.id} style={[officersStyles.row, { backgroundColor: c.secondary, borderColor: c.border }]}>
                  <View style={[officersStyles.avatar, { backgroundColor: c.successLight }]}>
                    <Feather name="file-text" size={12} color={c.success} />
                  </View>
                  <Text style={[officersStyles.name, { color: c.foreground }]}>{cit.violator_name}</Text>
                </View>
              ))}
            </View>
          </View>
        )}

        {/* Noise violation card */}
        {isNoiseViolation && (
          <NoiseViolationCard
            camera={assignment.cameraCode}
            confidence={assignment.confidence / 100}
          />
        )}

      </AutoScrollView>

      <Modal visible={showAllOfficers} animationType="fade" transparent onRequestClose={() => setShowAllOfficers(false)}>
        <Pressable style={officersStyles.overlay} onPress={() => setShowAllOfficers(false)}>
          <Pressable style={[officersStyles.sheet, { backgroundColor: c.card, borderColor: c.border }]} onPress={(e) => e.stopPropagation()}>
            <View style={officersStyles.header}>
              <View style={[officersStyles.iconWrap, { backgroundColor: c.successLight }]}>
                <Feather name="shield" size={16} color={c.success} />
              </View>
              <Text style={[officersStyles.title, { color: c.foreground }]}>
                Assigned Officers ({assignment.assignedOfficerNames.length})
              </Text>
              <Pressable onPress={() => setShowAllOfficers(false)} style={[officersStyles.closeBtn, { backgroundColor: c.secondary }]}>
                <Feather name="x" size={14} color={c.mutedForeground} />
              </Pressable>
            </View>
            <View style={{ gap: 8 }}>
              {assignment.assignedOfficerNames.map((name) => (
                <View key={name} style={[officersStyles.row, { backgroundColor: c.secondary, borderColor: c.border }]}>
                  <View style={[officersStyles.avatar, { backgroundColor: c.successLight }]}>
                    <Text style={{ color: c.success, fontSize: 11, fontFamily: "Inter_600SemiBold" }}>{name[0]}</Text>
                  </View>
                  <Text style={[officersStyles.name, { color: c.foreground }]}>{name}</Text>
                </View>
              ))}
            </View>
          </Pressable>
        </Pressable>
      </Modal>

      {canAct && (
        <View
          style={[
            styles.actionBar,
            { backgroundColor: c.card, borderTopColor: c.border, paddingBottom: insets.bottom + (Platform.OS === "web" ? 16 : 8) },
          ]}
        >
          {isMine && (
            <Pressable
              onPress={() => setDismissModalVisible(true)}
              disabled={busy}
              style={({ pressed }) => [styles.dismissBtn, { borderColor: c.destructive, opacity: pressed || busy ? 0.7 : 1 }]}
            >
              <Feather name="x" size={16} color={c.destructive} />
              <Text style={[styles.dismissBtnText, { color: c.destructive }]}>Dismiss</Text>
            </Pressable>
          )}

          <Pressable
            onPress={isUnassigned ? handleAccept : () => setResolveModalVisible(true)}
            disabled={busy}
            style={({ pressed }) => [
              styles.advanceBtn,
              { backgroundColor: isUnassigned ? c.primary : c.success, opacity: pressed || busy ? 0.85 : 1 },
            ]}
          >
            {busy ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <>
                <Feather name={isUnassigned ? "log-in" : "check-circle"} size={18} color="#fff" />
                <Text style={styles.advanceBtnText}>
                  {isUnassigned
                    ? "Accept Assignment"
                    : citationsForAlert.length > 0
                      ? `Resolve · ${citationsForAlert.length} filed`
                      : "Mark Resolved"}
                </Text>
              </>
            )}
          </Pressable>
        </View>
      )}

      <DismissModal visible={dismissModalVisible} onClose={() => setDismissModalVisible(false)} onConfirm={handleDismissConfirm} />
      <ResolveModal
        key={assignment.dbId}
        visible={resolveModalVisible}
        assignment={assignment}
        officerId={officer?.officerId ?? null}
        officerName={officer?.name ?? ""}
        existingCitations={citationsForAlert}
        onClose={() => setResolveModalVisible(false)}
        onFiled={refreshCitations}
        onFinished={handleFinished}
      />
    </View>
  );
}

const rStyles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: "flex-end" },
  overlay: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: "rgba(0,0,0,0.6)" },
  // No height here — it's set per-instance where used (a definite `height`,
  // not maxHeight: see ResolveModal, which needs a definite parent size for
  // its ScrollView's flex: 1 to resolve against).
  sheet: { borderTopLeftRadius: 24, borderTopRightRadius: 24, borderWidth: 1, borderBottomWidth: 0, paddingHorizontal: 20, paddingBottom: 36 },
  handle: { width: 40, height: 4, borderRadius: 2, alignSelf: "center", marginTop: 12, marginBottom: 8 },
  header: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 12 },
  iconWrap: { width: 44, height: 44, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 16, fontFamily: "Inter_700Bold" },
  subtitle: { fontSize: 12, fontFamily: "Inter_400Regular", marginTop: 2 },
  closeBtn: { width: 32, height: 32, borderRadius: 8, alignItems: "center", justifyContent: "center" },
  footerBtns: { flexDirection: "row", gap: 10 },
  cancelBtn: { paddingHorizontal: 18, paddingVertical: 13, borderRadius: 12, borderWidth: 1, alignItems: "center", justifyContent: "center" },
  cancelText: { fontSize: 14, fontFamily: "Inter_600SemiBold" },
  confirmBtn: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 13, borderRadius: 12, backgroundColor: "#10b981" },
  confirmText: { color: "#fff", fontSize: 14, fontFamily: "Inter_600SemiBold" },
});

const cfStyles = StyleSheet.create({
  justFiledBanner: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 12, paddingVertical: 12, borderRadius: 12, marginBottom: 12 },
  justFiledBannerText: { fontSize: 13, fontFamily: "Inter_700Bold", color: "#fff", flex: 1 },
  filedBanner: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, marginBottom: 14 },
  filedBannerText: { fontSize: 12, fontFamily: "Inter_600SemiBold", flex: 1 },
  lockedLine: { fontSize: 12, fontFamily: "Inter_400Regular", marginBottom: 16 },
  sectionLabel: { fontSize: 11, fontFamily: "Inter_600SemiBold", letterSpacing: 0.8, marginBottom: 8 },
  typeGrid: { gap: 8 },
  typeRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 14, paddingVertical: 14, borderRadius: 12, borderWidth: 1 },
  typeLabel: { fontSize: 15, fontFamily: "Inter_600SemiBold" },
  confirmedTypeRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 14, paddingVertical: 12, borderRadius: 12, borderWidth: 1, marginBottom: 8 },
  addAnotherLink: { flexDirection: "row", alignItems: "center", gap: 6, paddingVertical: 6 },
  addAnotherLinkText: { fontSize: 13, fontFamily: "Inter_600SemiBold" },
  // Underline treatment shared by every field in the form (name parts,
  // suffix/barangay pickers, notes) — transparent background, no border
  // except the 1px bottom rule. paddingVertical keeps the tap target large
  // even though the box itself is gone (one-handed, outdoor use).
  fieldGroup: { marginBottom: 14 },
  fieldLabel: { fontSize: 12, fontFamily: "Inter_500Medium", marginBottom: 6 },
  underlineInput: { borderBottomWidth: 1, paddingVertical: 14, fontSize: 15, fontFamily: "Inter_400Regular" },
  underlinePicker: { flexDirection: "row", alignItems: "center", gap: 10 },
  underlinePickerText: { flex: 1, fontSize: 15, fontFamily: "Inter_400Regular" },
  sameAsBadge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 20 },
  sameAsBadgeText: { fontSize: 10, fontFamily: "Inter_600SemiBold" },
  // Notes stays boxed (not underline, unlike the rest of the form) —
  // reverted per feedback: the underline treatment read worse here.
  notesInput: { borderWidth: 1, borderRadius: 12, padding: 14, fontSize: 15, fontFamily: "Inter_400Regular", minHeight: 90 },
  errorText: { fontSize: 13, fontFamily: "Inter_500Medium", marginTop: 12 },
  actionRow: { flexDirection: "row", gap: 10, marginTop: 16 },
  secondaryBtn: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 15, borderRadius: 12, borderWidth: 1 },
  secondaryBtnText: { fontSize: 14, fontFamily: "Inter_600SemiBold" },
  primaryBtn: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 15, borderRadius: 12 },
  primaryBtnText: { color: "#fff", fontSize: 14, fontFamily: "Inter_600SemiBold" },
  barangaySheet: { position: "absolute", bottom: 0, left: 0, right: 0, borderTopLeftRadius: 24, borderTopRightRadius: 24, borderWidth: 1, borderBottomWidth: 0, padding: 20, paddingBottom: 36, gap: 8 },
  barangayRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 16, paddingVertical: 16, borderRadius: 12, borderWidth: 1 },
  barangayRowText: { fontSize: 16, fontFamily: "Inter_600SemiBold" },
  reviewBlock: { marginTop: 12 },
  reviewLabel: { fontSize: 10, fontFamily: "Inter_600SemiBold", letterSpacing: 0.6, marginBottom: 3 },
  reviewValue: { fontSize: 14, fontFamily: "Inter_600SemiBold" },
});

const officersStyles = StyleSheet.create({
  overlay: { flex: 1, alignItems: "center", justifyContent: "center", padding: 20, backgroundColor: "rgba(0,0,0,0.6)" },
  sheet: { width: "100%", maxWidth: 320, borderRadius: 16, borderWidth: 1, padding: 16 },
  header: { flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 14 },
  iconWrap: { width: 32, height: 32, borderRadius: 8, alignItems: "center", justifyContent: "center" },
  title: { flex: 1, fontSize: 15, fontFamily: "Inter_700Bold" },
  closeBtn: { width: 28, height: 28, borderRadius: 8, alignItems: "center", justifyContent: "center" },
  row: { flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1 },
  avatar: { width: 24, height: 24, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  name: { fontSize: 13, fontFamily: "Inter_600SemiBold" },
});

const styles = StyleSheet.create({
  root: { flex: 1 },
  topBar: { flexDirection: "row", alignItems: "center", paddingHorizontal: 16, paddingBottom: 14, borderBottomWidth: 1, gap: 12 },
  backBtn: { padding: 4 },
  topBarTitle: { fontSize: 18, fontFamily: "Inter_600SemiBold", flex: 1 },
  statusPillTop: { borderWidth: 1, borderRadius: 20, paddingHorizontal: 10, paddingVertical: 4 },
  statusPillText: { fontSize: 12, fontFamily: "Inter_600SemiBold" },
  scroll: { padding: 16, gap: 12 },
  card: { borderRadius: 14, borderWidth: 1, padding: 16, gap: 14 },
  cardLabel: { fontSize: 11, fontFamily: "Inter_600SemiBold", letterSpacing: 0.8 },
  violationHeader: { flexDirection: "row", alignItems: "center", gap: 14 },
  iconCircle: { width: 56, height: 56, borderRadius: 16, alignItems: "center", justifyContent: "center" },
  violationType: { fontSize: 20, fontFamily: "Inter_700Bold" },
  codeText: { fontSize: 12, fontFamily: "Inter_400Regular", marginTop: 2 },
  description: { fontSize: 15, fontFamily: "Inter_400Regular", lineHeight: 22 },
  infoRow: { flexDirection: "row", alignItems: "flex-start", gap: 12, paddingBottom: 12, borderBottomWidth: 1 },
  infoLabel: { fontSize: 11, fontFamily: "Inter_400Regular", textTransform: "uppercase", letterSpacing: 0.5 },
  infoValue: { fontSize: 14, fontFamily: "Inter_500Medium", marginTop: 2 },
  actionBar: { flexDirection: "row", padding: 16, gap: 12, borderTopWidth: 1 },
  dismissBtn: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 16, paddingVertical: 14, borderRadius: 12, borderWidth: 1 },
  dismissBtnText: { fontSize: 15, fontFamily: "Inter_600SemiBold" },
  advanceBtn: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 14, borderRadius: 12 },
  advanceBtnText: { color: "#fff", fontSize: 15, fontFamily: "Inter_600SemiBold" },
  notFound: { marginTop: 12, fontSize: 16, fontFamily: "Inter_400Regular" },
  modalBackdrop: { flex: 1, justifyContent: "flex-end" },
  modalOverlay: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: "rgba(0,0,0,0.55)" },
  modalSheet: { borderTopLeftRadius: 20, borderTopRightRadius: 20, borderWidth: 1, borderBottomWidth: 0, padding: 20, paddingBottom: 36, gap: 14 },
  modalHandle: { width: 40, height: 4, borderRadius: 2, alignSelf: "center", marginBottom: 4 },
  modalHeader: { flexDirection: "row", alignItems: "center", gap: 12 },
  modalIconWrap: { width: 44, height: 44, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  modalTitle: { fontSize: 17, fontFamily: "Inter_700Bold" },
  modalSubtitle: { fontSize: 13, fontFamily: "Inter_400Regular", marginTop: 2 },
  modalSectionLabel: { fontSize: 11, fontFamily: "Inter_600SemiBold", letterSpacing: 0.8 },
  reasonGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  reasonChip: { flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 20, borderWidth: 1 },
  reasonText: { fontSize: 13, fontFamily: "Inter_500Medium" },
  modalInput: { borderWidth: 1, borderRadius: 12, padding: 12, fontSize: 14, fontFamily: "Inter_400Regular", minHeight: 80, textAlignVertical: "top" },
  modalActions: { flexDirection: "row", gap: 10, marginTop: 4 },
  modalCancelBtn: { paddingHorizontal: 20, paddingVertical: 14, borderRadius: 12, borderWidth: 1, alignItems: "center", justifyContent: "center" },
  modalCancelText: { fontSize: 15, fontFamily: "Inter_600SemiBold" },
  modalConfirmBtn: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 14, borderRadius: 12, borderWidth: 1 },
  modalConfirmText: { fontSize: 15, fontFamily: "Inter_600SemiBold" },
});
