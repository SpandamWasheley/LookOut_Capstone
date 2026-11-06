import React, { useEffect, useRef } from "react";
import { Animated, StyleSheet, View } from "react-native";

import { radius, space, SPINE } from "@/constants/theme";
import { useColors } from "@/hooks/useColors";

/**
 * A single pulsing placeholder bar.
 *
 * Why these exist at all: a list that is loading used to show one small
 * spinner in the middle of an empty screen, which tells the officer that
 * something is happening but nothing about what is coming. A skeleton in the
 * shape of the real content says "two cards, about this big, about to appear"
 * — and because it occupies the same space, the screen does not jump when the
 * data lands.
 *
 * The pulse is opacity only, so it runs on the native driver. A sweeping
 * gradient shimmer would need expo-linear-gradient, and a new native
 * dependency for a loading state is not a trade worth making.
 */
export function Skeleton({
  width,
  height = 12,
  rounding = 4,
}: {
  width: number | `${number}%`;
  height?: number;
  rounding?: number;
}) {
  const c = useColors();
  const pulse = useRef(new Animated.Value(0.4)).current;

  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 650, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0.4, duration: 650, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [pulse]);

  return (
    <Animated.View
      style={{
        width,
        height,
        borderRadius: rounding,
        backgroundColor: c.muted,
        opacity: pulse,
      }}
    />
  );
}

/**
 * The assignment list's loading state: the card's own silhouette, spine and
 * all, so the shape an officer is about to read is already on screen.
 */
export function AssignmentCardSkeleton() {
  const c = useColors();

  return (
    <View
      style={[
        styles.card,
        { backgroundColor: c.card, borderColor: c.border, borderRadius: radius.card },
      ]}
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
    >
      <View style={[styles.spine, { backgroundColor: c.border }]} />
      <View style={styles.body}>
        <View style={styles.top}>
          <Skeleton width={34} height={34} rounding={radius.control} />
          <View style={styles.meta}>
            <Skeleton width="55%" height={15} />
            <Skeleton width="34%" height={11} />
          </View>
        </View>
        <Skeleton width="42%" height={11} />
        <Skeleton width="92%" height={13} />
        <Skeleton width="70%" height={13} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { flexDirection: "row", borderWidth: 1, overflow: "hidden", marginBottom: space.md },
  spine: { width: SPINE },
  body: { flex: 1, padding: space.base, gap: space.sm },
  top: { flexDirection: "row", alignItems: "center", gap: space.md, marginBottom: space.xs },
  meta: { flex: 1, gap: space.sm },
});
