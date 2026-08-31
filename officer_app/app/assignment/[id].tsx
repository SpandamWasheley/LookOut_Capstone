import { Feather } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { Image } from "expo-image";
import { useLocalSearchParams, useRouter } from "expo-router";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
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
  dispatched: "Accepted",
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

function normalizeName(...parts: string[]): string {
  return parts.join(" ").trim().toLowerCase().replace(/\s+/g, " ");
}

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

  const [violationTypes, setViolationTypes] = useState<api.ApiViolationType[]>([]);
  const [typesLoading, setTypesLoading] = useState(true);
  const [selectedTypeIds, setSelectedTypeIds] = useState<Set<number>>(new Set());

  const [firstName, setFirstName] = useState("");
  const [middleName, setMiddleName] = useState("");
  const [lastName, setLastName] = useState("");
  const [suffix, setSuffix] = useState("");
  const [violatorBarangay, setViolatorBarangay] = useState<string | null>(null);
  const [carriedBarangay, setCarriedBarangay] = useState(false);
  const [notes, setNotes] = useState("");

  const [barangaySheetVisible, setBarangaySheetVisible] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [duplicateWarning, setDuplicateWarning] = useState<{ name: string; action: "finish" | "another" } | null>(null);
  const [formError, setFormError] = useState("");

  const lastNameRef = useRef<TextInput>(null);

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
        // Carry forward violation types + barangay; name and notes are
        // per-person and always cleared. Cursor goes straight to last name.
        setFirstName("");
        setMiddleName("");
        setLastName("");
        setSuffix("");
        setCarriedBarangay(true);
        setNotes("");
        requestAnimationFrame(() => lastNameRef.current?.focus());
      }
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save citation.");
    } finally {
      setSubmitting(false);
    }
  };

  const handlePress = (action: "finish" | "another") => {
    if (Platform.OS !== "web") Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    const dup = findDuplicate();
    if (dup) {
      setDuplicateWarning({ name: dup.violator_name, action });
      return;
    }
    doSubmit(action);
  };

  const hasFiledAny = existingCitations.length > 0;
  const selectedBarangayLabel = BARANGAY_OPTIONS.find((b) => b.value === violatorBarangay)?.label;

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <KeyboardAvoidingView style={rStyles.backdrop} behavior={Platform.OS === "ios" ? "padding" : "height"}>
        <Pressable style={rStyles.overlay} onPress={onClose} />
        <View style={[rStyles.sheet, { backgroundColor: c.card, borderColor: c.border, maxHeight: "92%" }]}>
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

          <ScrollView keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false} style={{ flexGrow: 0 }}>
            {hasFiledAny && (
              <View style={[cfStyles.filedBanner, { backgroundColor: c.successLight, borderColor: c.success }]}>
                <Feather name="check-circle" size={13} color={c.success} />
                <Text style={[cfStyles.filedBannerText, { color: c.success }]}>
                  {existingCitations.length} citation{existingCitations.length !== 1 ? "s" : ""} filed so far for this scene
                </Text>
              </View>
            )}

            <View style={cfStyles.lockedRow}>
              <View style={[cfStyles.lockedChip, { backgroundColor: c.muted, borderColor: c.border }]}>
                <Feather name="shield" size={12} color={c.mutedForeground} />
                <Text style={[cfStyles.lockedChipText, { color: c.mutedForeground }]}>{officerName || "Officer"}</Text>
              </View>
              <View style={[cfStyles.lockedChip, { backgroundColor: c.muted, borderColor: c.border }]}>
                <Feather name="map-pin" size={12} color={c.mutedForeground} />
                <Text style={[cfStyles.lockedChipText, { color: c.mutedForeground }]}>Tetuan (violation site)</Text>
              </View>
              <View style={[cfStyles.lockedChip, { backgroundColor: c.muted, borderColor: c.border }]}>
                <Feather name="clock" size={12} color={c.mutedForeground} />
                <Text style={[cfStyles.lockedChipText, { color: c.mutedForeground }]}>{formatDate(assignment.dispatchedAt)}</Text>
              </View>
            </View>

            <Text style={[cfStyles.sectionLabel, { color: c.mutedForeground }]}>VIOLATION TYPE(S)</Text>
            {typesLoading ? (
              <ActivityIndicator color={c.primary} style={{ marginVertical: 12 }} accessibilityLabel="Loading violation types" />
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
            <View style={cfStyles.nameRow}>
              <TextInput
                style={[cfStyles.input, cfStyles.nameInputWide, { color: c.foreground, borderColor: c.border, backgroundColor: c.muted }]}
                placeholder="First name"
                placeholderTextColor={c.mutedForeground}
                value={firstName}
                onChangeText={setFirstName}
              />
              <TextInput
                style={[cfStyles.input, cfStyles.nameInputWide, { color: c.foreground, borderColor: c.border, backgroundColor: c.muted }]}
                placeholder="Middle name (optional)"
                placeholderTextColor={c.mutedForeground}
                value={middleName}
                onChangeText={setMiddleName}
              />
            </View>
            <View style={cfStyles.nameRow}>
              <TextInput
                ref={lastNameRef}
                style={[cfStyles.input, cfStyles.nameInputWide, { color: c.foreground, borderColor: c.border, backgroundColor: c.muted }]}
                placeholder="Last name"
                placeholderTextColor={c.mutedForeground}
                value={lastName}
                onChangeText={setLastName}
              />
              <TextInput
                style={[cfStyles.input, cfStyles.nameInputNarrow, { color: c.foreground, borderColor: c.border, backgroundColor: c.muted }]}
                placeholder="Suffix"
                placeholderTextColor={c.mutedForeground}
                value={suffix}
                onChangeText={setSuffix}
              />
            </View>

            <Text style={[cfStyles.sectionLabel, { color: c.mutedForeground, marginTop: 18 }]}>VIOLATOR&apos;S HOME BARANGAY</Text>
            <Pressable
              onPress={() => setBarangaySheetVisible(true)}
              style={[cfStyles.pickerBtn, { backgroundColor: c.muted, borderColor: c.border }]}
              accessibilityRole="button"
              accessibilityLabel="Select violator's home barangay"
            >
              <Feather name="map-pin" size={15} color={c.mutedForeground} />
              <Text style={[cfStyles.pickerBtnText, { color: violatorBarangay ? c.foreground : c.mutedForeground }]}>
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
              accessibilityLabel="Save and add another citation"
              style={[
                cfStyles.secondaryBtn,
                hasFiledAny
                  ? { backgroundColor: c.info, borderColor: c.info }
                  : { backgroundColor: "transparent", borderColor: c.border },
                { opacity: !canSubmit ? 0.5 : 1 },
              ]}
            >
              <Feather name="user-plus" size={15} color={hasFiledAny ? "#fff" : c.foreground} />
              <Text style={[cfStyles.secondaryBtnText, { color: hasFiledAny ? "#fff" : c.foreground }]}>Save & add another</Text>
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

      <Modal visible={!!duplicateWarning} animationType="fade" transparent onRequestClose={() => setDuplicateWarning(null)}>
        <Pressable style={officersStyles.overlay} onPress={() => setDuplicateWarning(null)}>
          <Pressable
            style={[officersStyles.sheet, { backgroundColor: c.card, borderColor: c.border, alignItems: "center" }]}
            onPress={(e) => e.stopPropagation()}
          >
            <View style={[rStyles.iconWrap, { backgroundColor: c.warningLight, marginBottom: 4 }]}>
              <Feather name="alert-triangle" size={20} color={c.warning} />
            </View>
            <Text style={[rStyles.title, { color: c.foreground, marginTop: 8, textAlign: "center" }]}>Already cited for this alert</Text>
            <Text style={[rStyles.subtitle, { color: c.mutedForeground, textAlign: "center", marginTop: 4 }]}>
              You&apos;ve already cited a {duplicateWarning?.name} for this alert. File anyway?
            </Text>
            <View style={[rStyles.footerBtns, { marginTop: 16, width: "100%" }]}>
              <Pressable onPress={() => setDuplicateWarning(null)} style={[rStyles.cancelBtn, { borderColor: c.border }]}>
                <Text style={[rStyles.cancelText, { color: c.mutedForeground }]}>Cancel</Text>
              </Pressable>
              <Pressable
                onPress={() => {
                  const action = duplicateWarning!.action;
                  setDuplicateWarning(null);
                  doSubmit(action);
                }}
                style={rStyles.confirmBtn}
              >
                <Feather name="check-circle" size={16} color="#fff" />
                <Text style={rStyles.confirmText}>File anyway</Text>
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
  camera,
  zone,
  timestamp,
}: {
  imageUrl: string;
  camera: string | null;
  zone: string;
  timestamp: string;
}) {
  const c = useColors();
  const [playing, setPlaying] = useState(false);
  const [elapsed, setElapsed] = useState(18);
  const [fullscreen, setFullscreen] = useState(false);
  const duration = 45;

  useEffect(() => {
    if (!playing) return;
    const iv = setInterval(() => {
      setElapsed((p) => {
        if (p >= duration) { setPlaying(false); return duration; }
        return p + 0.5;
      });
    }, 500);
    return () => clearInterval(iv);
  }, [playing]);

  const fmtSec = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const pct = (elapsed / duration) * 100;
  const skip = (delta: number) => setElapsed((p) => Math.min(duration, Math.max(0, p + delta)));

  // Stable source object so expo-image never treats a parent re-render as a
  // source change (which would reload the image and flicker).
  const imageSource = useMemo(() => ({ uri: imageUrl }), [imageUrl]);

  const content = (
    <>
      <View style={[rpStyles.imageBox, fullscreen && rpStyles.imageBoxFullscreen]}>
        <Image source={imageSource} style={[rpStyles.image, { opacity: playing ? 0.82 : 0.55 }]} contentFit="cover" />
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
        <View style={[rpStyles.progressTrack, { backgroundColor: c.border }]}>
          <View style={[rpStyles.progressFill, { width: `${pct}%`, backgroundColor: c.primary }]} />
          <View style={[rpStyles.violationDot, { left: `${(18 / duration) * 100}%` }]} />
        </View>
        <View style={rpStyles.controlsRow}>
          <View style={rpStyles.controlsLeft}>
            <Pressable onPress={() => skip(-5)} hitSlop={8}>
              <Feather name="rotate-ccw" size={15} color={c.mutedForeground} />
            </Pressable>
            <Pressable onPress={() => setPlaying(!playing)} style={[rpStyles.playPauseBtn, { backgroundColor: c.primary }]}>
              <Feather name={playing ? "pause" : "play"} size={13} color="#0c0f16" style={playing ? undefined : { marginLeft: 1.5 }} />
            </Pressable>
            <Pressable onPress={() => skip(5)} hitSlop={8}>
              <Feather name="rotate-cw" size={15} color={c.mutedForeground} />
            </Pressable>
            <Text style={[rpStyles.timeText, { color: c.mutedForeground }]}>{fmtSec(elapsed)} / {fmtSec(duration)}</Text>
          </View>
          <View style={rpStyles.controlsRight}>
            <Pressable onPress={() => setFullscreen((f) => !f)} hitSlop={8}>
              <Feather name={fullscreen ? "minimize" : "maximize"} size={14} color={c.mutedForeground} />
            </Pressable>
            <Pressable style={rpStyles.saveClipBtn} hitSlop={8}>
              <Feather name="download" size={11} color={c.mutedForeground} />
              <Text style={[rpStyles.saveClipText, { color: c.mutedForeground }]}>Save clip</Text>
            </Pressable>
          </View>
        </View>
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
  violationDot: { position: "absolute", top: -3, width: 10, height: 10, borderRadius: 5, backgroundColor: "#ef4444" },
  controlsRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  controlsLeft: { flexDirection: "row", alignItems: "center", gap: 12 },
  controlsRight: { flexDirection: "row", alignItems: "center", gap: 14 },
  playPauseBtn: { width: 28, height: 28, borderRadius: 14, alignItems: "center", justifyContent: "center" },
  timeText: { fontSize: 11, fontFamily: "Inter_500Medium" },
  saveClipBtn: { flexDirection: "row", alignItems: "center", gap: 4 },
  saveClipText: { fontSize: 11, fontFamily: "Inter_500Medium" },
  fullscreenBackdrop: { flex: 1, backgroundColor: "#000", justifyContent: "center" },
  fullscreenWrap: { flex: 1 },
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
        {!!assignment.imageUrl && (
          <RecordingPlayer
            imageUrl={assignment.imageUrl}
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
          <Text style={[styles.cardLabel, { color: c.mutedForeground }]}>DISPATCH INFO</Text>
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
  sheet: { borderTopLeftRadius: 24, borderTopRightRadius: 24, borderWidth: 1, borderBottomWidth: 0, maxHeight: "85%", paddingHorizontal: 20, paddingBottom: 36 },
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
  filedBanner: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, marginBottom: 14 },
  filedBannerText: { fontSize: 12, fontFamily: "Inter_600SemiBold", flex: 1 },
  lockedRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 16 },
  lockedChip: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 10, paddingVertical: 7, borderRadius: 20, borderWidth: 1 },
  lockedChipText: { fontSize: 11, fontFamily: "Inter_500Medium" },
  sectionLabel: { fontSize: 11, fontFamily: "Inter_600SemiBold", letterSpacing: 0.8, marginBottom: 8 },
  typeGrid: { gap: 8 },
  typeRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 14, paddingVertical: 14, borderRadius: 12, borderWidth: 1 },
  typeLabel: { fontSize: 15, fontFamily: "Inter_600SemiBold" },
  nameRow: { flexDirection: "row", gap: 10, marginBottom: 10 },
  input: { borderWidth: 1, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 14, fontSize: 15, fontFamily: "Inter_400Regular" },
  nameInputWide: { flex: 1 },
  nameInputNarrow: { width: 88 },
  pickerBtn: { flexDirection: "row", alignItems: "center", gap: 10, borderWidth: 1, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 14 },
  pickerBtnText: { flex: 1, fontSize: 15, fontFamily: "Inter_400Regular" },
  sameAsBadge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 20 },
  sameAsBadgeText: { fontSize: 10, fontFamily: "Inter_600SemiBold" },
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
