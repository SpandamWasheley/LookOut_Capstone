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
  const nameParts = fullName.toLowerCase().split(/[\s,]+/).filter((p) => p.length > 2);
  if (nameParts.length === 0) return false;
  // Suspect can be several "; "-joined names — match against each one
  // individually and require ALL of a candidate's name parts to be present,
  // otherwise a shared surname (e.g. everyone in the same household) makes
  // every relative look like a match instead of just the tagged person(s).
  return suspect.split(";").some((entry) => {
    const e = entry.trim().toLowerCase();
    return e.length > 0 && nameParts.every((p) => e.includes(p));
  });
}

interface ResolveModalProps {
  visible: boolean;
  suspect: string;
  onClose: () => void;
  onConfirm: (selectedNames: string | null) => void;
  saveLabel?: string;
  mode?: "resolve" | "candidate";
}

function ResolveModal({ visible, suspect, onClose, onConfirm, saveLabel, mode = "resolve" }: ResolveModalProps) {
  const c = useColors();
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!visible) return;
    setLoading(true);
    setSelected(new Set());
    setSearch("");
    setConfirming(false);
    Promise.all([api.getHouseholds(), api.getResidents()])
      .then(([hhRes, resRes]) => {
        const households = Array.isArray(hhRes) ? hhRes : hhRes.results;
        const residents = Array.isArray(resRes) ? resRes : resRes.results;
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
        // Standalone residents — not part of any household, but web's picker
        // includes them too, so mobile must match to keep counts consistent.
        for (const r of residents) {
          const id = r.barangay_id ?? r.code;
          if (!id || seen.has(id)) continue;
          seen.add(id);
          const isPossible = suspectMatches(suspect, r.name ?? "");
          list.push({
            id,
            fullName: r.name ?? "",
            barangayId: id,
            household: "",
            age: r.age ?? null,
            isPossible,
          });
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
                      {item.barangayId}{item.age != null ? ` · Age ${item.age}` : ""}{item.household ? ` · ${item.household}` : ""}
                    </Text>
                  </View>
                </Pressable>
              );
            })}
          </ScrollView>

          <View style={rStyles.footer}>
            <Text style={[rStyles.footerHint, { color: c.mutedForeground }]}>
              {selected.size === 0
                ? (mode === "candidate" ? "Select at least one resident to continue" : "No residents selected — resolves without linking")
                : `${selected.size} resident${selected.size !== 1 ? "s" : ""} selected`}
            </Text>
            <View style={rStyles.footerBtns}>
              <Pressable onPress={onClose} style={[rStyles.cancelBtn, { borderColor: c.border }]}>
                <Text style={[rStyles.cancelText, { color: c.mutedForeground }]}>Cancel</Text>
              </Pressable>
              <Pressable
                onPress={() => setConfirming(true)}
                disabled={mode === "candidate" && selected.size === 0}
                style={[rStyles.confirmBtn, { opacity: mode === "candidate" && selected.size === 0 ? 0.5 : 1 }]}
              >
                <Feather name="check-circle" size={16} color="#fff" />
                <Text style={rStyles.confirmText}>{saveLabel ?? "Confirm & Resolve"}</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </KeyboardAvoidingView>

      <Modal visible={confirming} animationType="fade" transparent onRequestClose={() => setConfirming(false)}>
        <Pressable style={officersStyles.overlay} onPress={() => setConfirming(false)}>
          <Pressable style={[officersStyles.sheet, { backgroundColor: c.card, borderColor: c.border, alignItems: "center" }]} onPress={(e) => e.stopPropagation()}>
            <View style={[rStyles.iconWrap, { backgroundColor: "rgba(16,185,129,0.12)", marginBottom: 4 }]}>
              <Feather name="check-circle" size={20} color="#10b981" />
            </View>
            <Text style={[rStyles.title, { color: c.foreground, marginTop: 8 }]}>
              {mode === "candidate" ? "Set this candidate match?" : "Resolve this violation?"}
            </Text>
            <Text style={[rStyles.subtitle, { color: c.mutedForeground, textAlign: "center", marginTop: 4 }]}>
              {mode === "candidate"
                ? `${selected.size} resident${selected.size !== 1 ? "s" : ""} will be linked to this violation.`
                : (selected.size === 0
                  ? "No residents will be linked to this record."
                  : `${selected.size} resident${selected.size !== 1 ? "s" : ""} will be linked to this record.`) + " This action cannot be undone."}
            </Text>
            <View style={[rStyles.footerBtns, { marginTop: 16, width: "100%" }]}>
              <Pressable onPress={() => setConfirming(false)} style={[rStyles.cancelBtn, { borderColor: c.border }]}>
                <Text style={[rStyles.cancelText, { color: c.mutedForeground }]}>Cancel</Text>
              </Pressable>
              <Pressable onPress={() => { setConfirming(false); handleConfirm(); }} style={rStyles.confirmBtn}>
                <Feather name="check-circle" size={16} color="#fff" />
                <Text style={rStyles.confirmText}>{mode === "candidate" ? "Confirm" : "Yes, resolve"}</Text>
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

  const content = (
    <>
      <View style={[rpStyles.imageBox, fullscreen && rpStyles.imageBoxFullscreen]}>
        <Image source={{ uri: imageUrl }} style={[rpStyles.image, { opacity: playing ? 0.82 : 0.55 }]} contentFit="cover" />
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

  const isNoiseViolation = assignment?.violationType.code === "noise";

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
        undefined,
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
  suspectRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10 },
  suspectText: { fontSize: 13, fontFamily: "Inter_500Medium" },
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
