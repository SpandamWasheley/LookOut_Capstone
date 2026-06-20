import { Feather } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { useRouter } from "expo-router";
import React from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { getViolationIconName } from "@/constants/violationIcons";
import { Assignment } from "@/context/AssignmentContext";
import { useColors } from "@/hooks/useColors";

function timeAgo(isoString: string): string {
  const diff = Math.floor((Date.now() - new Date(isoString).getTime()) / 1000);
  if (diff < 0) return "just now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const STATUS_LABELS: Record<Assignment["status"], string> = {
  active: "Pending",
  dispatched: "Accepted",
  resolved: "Resolved",
  acknowledged: "Dismissed",
};

interface Props {
  assignment: Assignment;
}

export default function AssignmentCard({ assignment }: Props) {
  const router = useRouter();
  const c = useColors();

  const statusColor =
    assignment.status === "active"
      ? c.danger
      : assignment.status === "resolved" || assignment.status === "acknowledged"
        ? c.mutedForeground
        : c.accent;

  return (
    <Pressable
      onPress={() => {
        if (Platform.OS !== "web") Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
        router.push(`/assignment/${assignment.id}`);
      }}
      style={({ pressed }) => [
        styles.card,
        { backgroundColor: c.card, borderColor: c.border, opacity: pressed ? 0.85 : 1 },
      ]}
    >
      <View style={styles.leftBar}>
        <View style={[styles.priorityBar, { backgroundColor: assignment.violationType.color }]} />
      </View>
      <View style={styles.content}>
        <View style={styles.header}>
          <View style={[styles.iconWrap, { backgroundColor: `${assignment.violationType.color}22` }]}>
            <Feather name={getViolationIconName(assignment.violationType.icon)} size={18} color={assignment.violationType.color} />
          </View>
          <View style={styles.meta}>
            <Text style={[styles.type, { color: c.foreground }]} numberOfLines={1}>
              {assignment.violationType.label}
            </Text>
            <Text style={[styles.time, { color: c.mutedForeground }]}>{timeAgo(assignment.dispatchedAt)}</Text>
          </View>
          <View style={[styles.statusPill, { borderColor: statusColor }]}>
            <Text style={[styles.statusText, { color: statusColor }]}>{STATUS_LABELS[assignment.status]}</Text>
          </View>
        </View>
        <Text style={[styles.location, { color: c.mutedForeground }]} numberOfLines={1}>
          <Feather name="map-pin" size={11} color={c.mutedForeground} /> {assignment.location}
        </Text>
        <Text style={[styles.description, { color: c.foreground }]} numberOfLines={2}>
          {assignment.description}
        </Text>
        <View style={styles.footer}>
          <Text style={[styles.code, { color: c.mutedForeground }]}>{assignment.code}</Text>
          <Text style={[styles.confidence, { color: c.mutedForeground }]}>{assignment.confidence}% conf.</Text>
        </View>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: "row",
    borderRadius: 12,
    borderWidth: 1,
    marginBottom: 10,
    overflow: "hidden",
  },
  leftBar: { width: 4 },
  priorityBar: { flex: 1, width: 4 },
  content: { flex: 1, padding: 14, gap: 6 },
  header: { flexDirection: "row", alignItems: "center", gap: 10 },
  iconWrap: { width: 36, height: 36, borderRadius: 10, alignItems: "center", justifyContent: "center" },
  meta: { flex: 1 },
  type: { fontSize: 15, fontFamily: "Inter_600SemiBold" },
  time: { fontSize: 12, fontFamily: "Inter_400Regular", marginTop: 1 },
  statusPill: { borderWidth: 1, borderRadius: 20, paddingHorizontal: 8, paddingVertical: 3 },
  statusText: { fontSize: 11, fontFamily: "Inter_600SemiBold" },
  location: { fontSize: 12, fontFamily: "Inter_400Regular" },
  description: { fontSize: 13, fontFamily: "Inter_400Regular", lineHeight: 19 },
  footer: { flexDirection: "row", justifyContent: "space-between", marginTop: 2 },
  code: { fontSize: 11, fontFamily: "Inter_400Regular" },
  confidence: { fontSize: 11, fontFamily: "Inter_400Regular" },
});
