import { Feather } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { Image } from "expo-image";
import { useLocalSearchParams, useRouter } from "expo-router";
import React, { useEffect, useState } from "react";
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

interface Candidate {
  id: string;
  fullName: string;
  barangayId: string;
  household: string;
  age: number | null;
  isPossible: boolean;
}

function calcAge(birthdate?: string | null): number | null {
  if (!birthdate) return null;
  const dob = new Date(birthdate);
  const today = new Date();
  let age = today.getFullYear() - dob.getFullYear();
  if (
    today.getMonth() < dob.getMonth() ||
    (today.getMonth() === dob.getMonth() && today.getDate() < dob.getDate())
  ) age--;
  return age;
}

function suspectMatches(suspect: string, fullName: string): boolean {
  if (!suspect || !fullName) return false;
  const s = suspect.toLowerCase();
  return fullName.toLowerCase().split(/[\s,]+/).filter((p) => p.length > 2).some((p) => s.includes(p));
}

interface ResolveModalProps {
  visible: boolean;
  suspect: string;
  onClose: () => void;
  onConfirm: (selectedNames: string | null) => void;
  saveLabel?: string;
}

function ResolveModal({ visible, suspect, onClose, onConfirm, saveLabel }: ResolveModalProps) {
  const c = useColors();
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");

  useEffect(() => {
    if (!visible) return;
    setLoading(true);
    setSelected(new Set());
    setSearch("");
    api.getHouseholds()
      .then((res) => {
        const households = Array.isArray(res) ? res : res.results;
        const seen = new Set<string>();
        const list: Candidate[] = [];
        for (const hh of households) {
          for (const m of hh.members ?? []) {
            const id = m.barangay_id ?? m.code;
            if (!id || seen.has(id)) continue;
            seen.add(id);
            const fullName = `${m.first_name ?? ""} ${m.last_name ?? ""}`.trim();
            const isPossible = suspectMatches(suspect, fullName);
            list.push({
              id,
              fullName,
              barangayId: id,
              household: `${hh.family_name} household`,
              age: calcAge(m.birthdate),
              isPossible,
            });
          }
        }
        list.sort((a, b) => (a.isPossible === b.isPossible ? 0 : a.isPossible ? -1 : 1));
        setCandidates(list);
        const autoSelect = new Set(list.filter((c) => c.isPossible).map((c) => c.id));
        setSelected(autoSelect);
      })
      .catch(() => setCandidates([]))
      .finally(() => setLoading(false));
  }, [visible, suspect]);

  const toggle = (id: string) =>
    setSelected((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const filtered = candidates.filter((c) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return c.fullName.toLowerCase().includes(q) || c.household.toLowerCase().includes(q) || c.barangayId.toLowerCase().includes(q);
  });

  const handleConfirm = () => {
    const names = candidates.filter((c) => selected.has(c.id)).map((c) => c.fullName).join("; ");
    onConfirm(names || null);
  };

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <KeyboardAvoidingView style={rStyles.backdrop} behavior={Platform.OS === "ios" ? "padding" : "height"}>
        <Pressable style={rStyles.overlay} onPress={onClose} />
        <View style={[rStyles.sheet, { backgroundColor: c.card, borderColor: c.border }]}>
          <View style={[rStyles.handle, { backgroundColor: c.border }]} />

          <View style={rStyles.header}>
            <View style={[rStyles.iconWrap, { backgroundColor: "rgba(16,185,129,0.12)" }]}>
              <Feather name="users" size={20} color="#10b981" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={[rStyles.title, { color: c.foreground }]}>Confirm Resolution</Text>
              <Text style={[rStyles.subtitle, { color: c.mutedForeground }]}>Select involved resident(s)</Text>
            </View>
            <Pressable onPress={onClose} style={[rStyles.closeBtn, { backgroundColor: c.secondary }]}>
              <Feather name="x" size={16} color={c.mutedForeground} />
            </Pressable>
          </View>

          <View style={[rStyles.searchWrap, { backgroundColor: c.muted, borderColor: c.border }]}>
            <Feather name="search" size={14} color={c.mutedForeground} />
            <TextInput
              style={[rStyles.searchInput, { color: c.foreground }]}
              placeholder="Search residents…"
              placeholderTextColor={c.mutedForeground}
              value={search}
              onChangeText={setSearch}
            />
          </View>

          <ScrollView style={rStyles.list} showsVerticalScrollIndicator={false}>
            {loading ? (
              <ActivityIndicator color="#10b981" style={{ marginTop: 24 }} />
            ) : filtered.length === 0 ? (
              <Text style={[rStyles.emptyText, { color: c.mutedForeground }]}>
                {candidates.length === 0 ? "No residents found" : "No matches"}
              </Text>
            ) : filtered.map((item) => {
              const isChecked = selected.has(item.id);
              return (
                <Pressable
                  key={item.id}
                  onPress={() => toggle(item.id)}
                  style={({ pressed }) => [
                    rStyles.row,
                    {
                      backgroundColor: isChecked ? "rgba(16,185,129,0.07)" : c.secondary,
                      borderColor: isChecked ? "rgba(16,185,129,0.3)" : c.border,
                      opacity: pressed ? 0.8 : 1,
                    },
                  ]}
                >
                  <View style={[rStyles.checkbox, { backgroundColor: isChecked ? "#10b981" : "transparent", borderColor: isChecked ? "#10b981" : c.mutedForeground }]}>
                    {isChecked && <Feather name="check" size={10} color="#fff" />}
                  </View>
                  <View style={{ flex: 1 }}>
                    <View style={rStyles.nameRow}>
                      <Text style={[rStyles.name, { color: c.foreground }]}>{item.fullName}</Text>
                      {item.isPossible && (
                        <View style={rStyles.candidateBadge}>
                          <Text style={rStyles.candidateText}>Possible candidate</Text>
                        </View>
                      )}
                    </View>
                    <Text style={[rStyles.sub, { color: c.mutedForeground }]}>
                      {item.barangayId}{item.age != null ? ` · Age ${item.age}` : ""} · {item.household}
                    </Text>
                  </View>
                </Pressable>
              );
            })}
          </ScrollView>

          <View style={rStyles.footer}>
            <Text style={[rStyles.footerHint, { color: c.mutedForeground }]}>
              {selected.size === 0 ? "No residents selected" : `${selected.size} resident${selected.size !== 1 ? "s" : ""} selected`}
            </Text>
            <View style={rStyles.footerBtns}>
              <Pressable onPress={onClose} style={[rStyles.cancelBtn, { borderColor: c.border }]}>
                <Text style={[rStyles.cancelText, { color: c.mutedForeground }]}>Cancel</Text>
              </Pressable>
              <Pressable onPress={handleConfirm} style={rStyles.confirmBtn}>
                <Feather name="check-circle" size={16} color="#fff" />
                <Text style={rStyles.confirmText}>{saveLabel ?? "Confirm & Resolve"}</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

export default function AssignmentDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { getAssignment, acceptAssignment, resolveAssignment, dismissAssignment, saveNote } = useAssignments();
  const { officer } = useAuth();
  const c = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const assignment = getAssignment(id ?? "");
  const [note, setNote] = useState(assignment?.notes ?? "");
  const [editingNote, setEditingNote] = useState(false);
  const [dismissModalVisible, setDismissModalVisible] = useState(false);
  const [resolveModalVisible, setResolveModalVisible] = useState(false);
  const [setCandidateVisible, setSetCandidateVisible] = useState(false);
  const [busy, setBusy] = useState(false);

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

  const handleResolve = async (suspectNames: string | null) => {
    setResolveModalVisible(false);
    setBusy(true);
    if (Platform.OS !== "web") Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    try {
      await resolveAssignment(
        assignment.id,
        note || assignment.notes,
        suspectNames ?? undefined,
      );
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

  const handleSaveNote = async () => {
    await saveNote(assignment.id, note);
    setEditingNote(false);
    if (Platform.OS !== "web") Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
  };

  const handleUpdateSuspect = async (names: string | null) => {
    try {
      await api.updateAlert(assignment.dbId, { suspect: names ?? "" });
      setSetCandidateVisible(false);
    } catch {}
  };

  return (
    <View style={[styles.root, { backgroundColor: c.background }]}>
      <View
        style={[
          styles.topBar,
          { backgroundColor: c.card, borderBottomColor: c.border, paddingTop: insets.top + (Platform.OS === "web" ? 12 : 0) },
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

      <ScrollView
        contentContainerStyle={[styles.scroll, { paddingBottom: insets.bottom + (Platform.OS === "web" ? 34 : 120) }]}
        showsVerticalScrollIndicator={false}
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
          <Text style={[styles.description, { color: c.foreground }]}>{assignment.description}</Text>
          {/* Potential Candidates */}
          <View style={[styles.candidateBox, { backgroundColor: c.muted, borderColor: c.border }]}>
            <View style={styles.candidateHeader}>
              <Text style={[styles.candidateLabel, { color: c.mutedForeground }]}>Potential Candidates</Text>
              <Pressable
                onPress={() => setSetCandidateVisible(true)}
                style={[styles.candidateEditBtn, { backgroundColor: "rgba(59,130,246,0.12)", borderColor: "rgba(59,130,246,0.25)" }]}
              >
                <Feather name="edit-2" size={10} color="#3b82f6" />
                <Text style={styles.candidateEditText}>{assignment.suspect ? "Edit" : "+ Add"}</Text>
              </Pressable>
            </View>
            {assignment.suspect ? (
              <View style={styles.chipRow}>
                {assignment.suspect.split(";").map((n) => n.trim()).filter(Boolean).map((name) => (
                  <View key={name} style={[styles.chip, { backgroundColor: "rgba(59,130,246,0.1)", borderColor: "rgba(59,130,246,0.25)" }]}>
                    <Feather name="user" size={10} color="#3b82f6" />
                    <Text style={[styles.chipText, { color: "#3b82f6" }]}>{name}</Text>
                  </View>
                ))}
              </View>
            ) : (
              <Text style={[styles.candidateEmpty, { color: c.mutedForeground }]}>None set</Text>
            )}
          </View>
        </View>

        {!!assignment.imageUrl && (
          <View style={[styles.evidenceCard, { backgroundColor: c.card, borderColor: c.border }]}>
            <Image source={{ uri: assignment.imageUrl }} style={styles.evidenceImage} contentFit="cover" />
            <View style={styles.evidenceFooter}>
              <Feather name="camera" size={12} color={c.mutedForeground} />
              <Text style={[styles.evidenceText, { color: c.mutedForeground }]}>Evidence captured by {assignment.cameraCode ?? "camera"}</Text>
            </View>
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
            { icon: "user" as const, label: "Assigned To", value: assignment.assignedOfficerNames.length ? assignment.assignedOfficerNames.join(", ") : "Unassigned" },
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
          <View style={styles.notesHeader}>
            <Text style={[styles.cardLabel, { color: c.mutedForeground }]}>FIELD NOTES</Text>
            {!isClosed && !editingNote && (
              <Pressable onPress={() => setEditingNote(true)}>
                <Feather name="edit-2" size={15} color={c.accent} />
              </Pressable>
            )}
          </View>
          {editingNote ? (
            <View style={{ gap: 10 }}>
              <TextInput
                style={[styles.noteInput, { color: c.foreground, borderColor: c.border, backgroundColor: c.muted }]}
                value={note}
                onChangeText={setNote}
                placeholder="Add field notes..."
                placeholderTextColor={c.mutedForeground}
                multiline
                numberOfLines={4}
                textAlignVertical="top"
              />
              <View style={styles.noteActions}>
                <Pressable
                  onPress={() => { setEditingNote(false); setNote(assignment.notes); }}
                  style={[styles.noteBtn, { borderColor: c.border }]}
                >
                  <Text style={[styles.noteBtnText, { color: c.mutedForeground }]}>Cancel</Text>
                </Pressable>
                <Pressable onPress={handleSaveNote} style={[styles.noteBtn, { backgroundColor: c.accent, borderColor: c.accent }]}>
                  <Text style={[styles.noteBtnText, { color: "#fff" }]}>Save</Text>
                </Pressable>
              </View>
            </View>
          ) : (
            <Text style={[styles.noteText, { color: note ? c.foreground : c.mutedForeground }]}>
              {note || "No notes added yet"}
            </Text>
          )}
        </View>
      </ScrollView>

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
                <Text style={styles.advanceBtnText}>{isUnassigned ? "Accept Assignment" : "Mark Resolved"}</Text>
              </>
            )}
          </Pressable>
        </View>
      )}

      <DismissModal visible={dismissModalVisible} onClose={() => setDismissModalVisible(false)} onConfirm={handleDismissConfirm} />
      <ResolveModal
        visible={resolveModalVisible}
        suspect={assignment.suspect}
        onClose={() => setResolveModalVisible(false)}
        onConfirm={handleResolve}
      />
      <ResolveModal
        visible={setCandidateVisible}
        suspect={assignment.suspect}
        onClose={() => setSetCandidateVisible(false)}
        onConfirm={handleUpdateSuspect}
        saveLabel="Save Candidates"
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
  searchWrap: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, marginBottom: 10 },
  searchInput: { flex: 1, fontSize: 14, fontFamily: "Inter_400Regular" },
  list: { flexGrow: 0, marginBottom: 12 },
  emptyText: { textAlign: "center", paddingVertical: 24, fontSize: 14, fontFamily: "Inter_400Regular" },
  row: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12, borderRadius: 12, borderWidth: 1, marginBottom: 8 },
  checkbox: { width: 20, height: 20, borderRadius: 6, borderWidth: 1.5, alignItems: "center", justifyContent: "center" },
  nameRow: { flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap" },
  name: { fontSize: 14, fontFamily: "Inter_600SemiBold" },
  candidateBadge: { backgroundColor: "rgba(245,158,11,0.15)", paddingHorizontal: 7, paddingVertical: 2, borderRadius: 20 },
  candidateText: { fontSize: 10, fontFamily: "Inter_600SemiBold", color: "#f59e0b" },
  sub: { fontSize: 11, fontFamily: "Inter_400Regular", marginTop: 2 },
  footer: { gap: 10 },
  footerHint: { fontSize: 12, fontFamily: "Inter_400Regular" },
  footerBtns: { flexDirection: "row", gap: 10 },
  cancelBtn: { paddingHorizontal: 18, paddingVertical: 13, borderRadius: 12, borderWidth: 1, alignItems: "center", justifyContent: "center" },
  cancelText: { fontSize: 14, fontFamily: "Inter_600SemiBold" },
  confirmBtn: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 13, borderRadius: 12, backgroundColor: "#10b981" },
  confirmText: { color: "#fff", fontSize: 14, fontFamily: "Inter_600SemiBold" },
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
  suspectRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10 },
  suspectText: { fontSize: 13, fontFamily: "Inter_500Medium" },
  candidateBox: { borderRadius: 10, borderWidth: 1, padding: 12, gap: 8 },
  candidateHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  candidateLabel: { fontSize: 11, fontFamily: "Inter_600SemiBold", textTransform: "uppercase", letterSpacing: 0.5 },
  candidateEditBtn: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, paddingVertical: 4, borderRadius: 20, borderWidth: 1 },
  candidateEditText: { fontSize: 11, fontFamily: "Inter_600SemiBold", color: "#3b82f6" },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, paddingVertical: 4, borderRadius: 20, borderWidth: 1 },
  chipText: { fontSize: 12, fontFamily: "Inter_500Medium" },
  candidateEmpty: { fontSize: 12, fontFamily: "Inter_400Regular" },
  evidenceCard: { borderRadius: 14, borderWidth: 1, overflow: "hidden" },
  evidenceImage: { width: "100%", aspectRatio: 16 / 9, backgroundColor: "#000" },
  evidenceFooter: { flexDirection: "row", alignItems: "center", gap: 6, padding: 12 },
  evidenceText: { fontSize: 12, fontFamily: "Inter_400Regular" },
  infoRow: { flexDirection: "row", alignItems: "flex-start", gap: 12, paddingBottom: 12, borderBottomWidth: 1 },
  infoLabel: { fontSize: 11, fontFamily: "Inter_400Regular", textTransform: "uppercase", letterSpacing: 0.5 },
  infoValue: { fontSize: 14, fontFamily: "Inter_500Medium", marginTop: 2 },
  notesHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  noteText: { fontSize: 14, fontFamily: "Inter_400Regular", lineHeight: 21 },
  noteInput: { borderWidth: 1, borderRadius: 10, padding: 12, fontSize: 14, fontFamily: "Inter_400Regular", minHeight: 90 },
  noteActions: { flexDirection: "row", gap: 10, justifyContent: "flex-end" },
  noteBtn: { paddingHorizontal: 18, paddingVertical: 9, borderRadius: 10, borderWidth: 1 },
  noteBtnText: { fontSize: 14, fontFamily: "Inter_600SemiBold" },
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
