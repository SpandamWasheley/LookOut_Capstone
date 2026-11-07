import { Feather } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { useRouter } from "expo-router";
import React from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { radius, space, SPINE, type as T } from "@/constants/theme";
import { getViolationIconName } from "@/constants/violationIcons";
import { Assignment } from "@/context/AssignmentContext";
import { useColors } from "@/hooks/useColors";
import { readable, wash } from "@/lib/tint";

/**
 * Whole words, not "3m ago".
 *
 * The abbreviation saved nothing on a line with this much room, and an officer
 * scanning a list under time pressure should not have to expand "21m" into a
 * duration. Minutes matter here — a vehicle parked 6 minutes ago and one
 * parked 40 minutes ago are different situations — so the unit is never
 * dropped.
 */
function timeAgo(isoString: string): string {
  const seconds = Math.floor((Date.now() - new Date(isoString).getTime()) / 1000);
  if (seconds < 45) return "just now";
  const plural = (n: number, unit: string) => n + " " + unit + (n === 1 ? "" : "s") + " ago";
  if (seconds < 3600) return plural(Math.floor(seconds / 60), "minute");
  if (seconds < 86400) return plural(Math.floor(seconds / 3600), "hour");
  return plural(Math.floor(seconds / 86400), "day");
}

interface Props {
  assignment: Assignment;
}

export default function AssignmentCard({ assignment }: Props) {
  const router = useRouter();
  const c = useColors();

  /**
   * Status in the officer's language, not the database's.
   *
   * "Pending" described the record, not the officer. What an officer needs to
   * know is whether this one is waiting on THEM, so `active` says so and
   * `dispatched` reads "Assigned" -- it is on their plate, out of the queue.
   *
   * Dismissed is deliberately not red. It means the alert was judged a false
   * alarm, and colouring a false alarm like a live violation is the one
   * reading here that would be actively misleading.
   */
  const status = {
    active: { label: "Needs you", color: c.urgent },
    dispatched: { label: "Assigned", color: c.closed },
    resolved: { label: "Resolved", color: c.closed },
    acknowledged: { label: "Dismissed", color: c.mutedForeground },
  }[assignment.status];

  const level =
    assignment.levelLabel === "Likely"
      ? c.urgent
      : assignment.levelLabel === "Possible"
        ? c.pending
        : c.mutedForeground;

  const typeColor = assignment.violationType.color;

  return (
    <Pressable
      onPress={() => {
        if (Platform.OS !== "web") Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
        router.push(`/assignment/${assignment.id}`);
      }}
      accessibilityRole="button"
      accessibilityLabel={`${assignment.violationType.label} at ${assignment.location}. ${status.label}.`}
      style={({ pressed }) => [
        styles.card,
        {
          backgroundColor: c.card,
          borderColor: c.border,
          borderRadius: radius.card,
          opacity: pressed ? 0.85 : 1,
        },
        c.lift(1),
      ]}
    >
      {/* The spine. Coloured by violation type, so the list is scannable by
          colour before a single word has been read. */}
      <View style={[styles.spine, { backgroundColor: typeColor }]} />

      <View style={styles.body}>
        <View style={styles.top}>
          <View style={[styles.icon, { backgroundColor: wash(typeColor, c.scheme) }]}>
            <Feather
              name={getViolationIconName(assignment.violationType.icon)}
              size={17}
              color={readable(typeColor, c.scheme)}
            />
          </View>
          <View style={styles.name}>
            <Text style={[T.heading, { color: c.foreground }]} numberOfLines={1}>
              {assignment.violationType.label}
            </Text>
            <Text style={[T.meta, { color: c.mutedForeground }]}>
              {timeAgo(assignment.dispatchedAt)}
            </Text>
          </View>
          {/* A dot as well as the word: colour reads before text does, and
              this is the one thing on the card worth reading first. */}
          <View style={styles.status}>
            <View style={[styles.dot, { backgroundColor: status.color }]} />
            <Text style={[T.metaStrong, { color: status.color }]}>{status.label}</Text>
          </View>
        </View>

        <View style={styles.where}>
          <Feather name="map-pin" size={12} color={c.mutedForeground} />
          <Text style={[T.meta, { color: c.mutedForeground }]} numberOfLines={1}>
            {assignment.location}
          </Text>
        </View>

        <Text style={[T.body, styles.desc, { color: c.foreground }]} numberOfLines={2}>
          {assignment.description}
        </Text>

        {/* The code and the confidence level are a different kind of
            information from the description — a reference and a judgement,
            not a narrative — so a hairline separates them instead of a gap
            the eye has to interpret. */}
        <View style={[styles.foot, { borderTopColor: c.border }]}>
          <Text style={[T.code, { color: c.mutedForeground }]}>{assignment.code}</Text>
          {assignment.levelLabel ? (
            <Text style={[T.metaStrong, { color: level }]}>{assignment.levelLabel}</Text>
          ) : null}
        </View>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: { flexDirection: "row", borderWidth: 1, marginBottom: space.md, overflow: "hidden" },
  spine: { width: SPINE },
  body: { flex: 1, padding: space.base, minWidth: 0 },
  top: { flexDirection: "row", alignItems: "center", gap: space.md },
  icon: {
    width: 34,
    height: 34,
    borderRadius: radius.control,
    alignItems: "center",
    justifyContent: "center",
  },
  name: { flex: 1, minWidth: 0, gap: space.xxs },
  status: { flexDirection: "row", alignItems: "center", gap: space.xs },
  dot: { width: 7, height: 7, borderRadius: radius.pill },
  where: { flexDirection: "row", alignItems: "center", gap: space.xs, marginTop: space.md },
  desc: { marginTop: space.xs },
  foot: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginTop: space.md,
    paddingTop: space.md,
    borderTopWidth: 1,
  },
});
