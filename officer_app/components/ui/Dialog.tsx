import React, { useEffect, useRef } from "react";
import { Animated, Modal, Pressable, StyleSheet, Text, View } from "react-native";

import { duration, radius, space, type as T } from "@/constants/theme";
import { useColors } from "@/hooks/useColors";
import { Button } from "@/components/ui/Button";

interface Props {
  visible: boolean;
  title: string;
  message?: string;
  confirmLabel: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
  /** `danger` for anything that destroys or closes something. */
  tone?: "primary" | "danger";
  loading?: boolean;
}

/**
 * The confirmation dialog.
 *
 * Replaces Alert.alert, which draws the OPERATING SYSTEM's dialog — on Android
 * that is Roboto, Material's blue, and capitalised text buttons, none of which
 * appear anywhere else in LookOut. It was the single most out-of-place thing
 * in the app: the officer taps Sign Out in a hand-built screen and a stock
 * Android panel answers.
 *
 * It also could not be written properly. Alert.alert's body text had to carry
 * the question ("Are you sure you want to sign out?") because its buttons are
 * just labels; here the title asks and the buttons answer, which frees the
 * body to say something the officer does not already know — what signing out
 * will cost them.
 *
 * Buttons are STACKED, not side by side: a row of two puts the destructive one
 * under the right thumb, which is where an officer's thumb already rests.
 */
export function Dialog({
  visible,
  title,
  message,
  confirmLabel,
  cancelLabel = "Cancel",
  onConfirm,
  onCancel,
  tone = "primary",
  loading,
}: Props) {
  const c = useColors();
  const enter = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    // Only on the way in. Modal's own fade handles the exit, and animating
    // out here would hold the sheet on screen after the officer has answered.
    if (visible) {
      enter.setValue(0);
      Animated.timing(enter, {
        toValue: 1,
        duration: duration.sheet,
        useNativeDriver: true,
      }).start();
    }
  }, [visible, enter]);

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onCancel}>
      {/* Tapping the scrim cancels — the same answer the back button gives,
          so neither can be mistaken for confirming. */}
      <Pressable style={styles.scrim} onPress={loading ? undefined : onCancel} accessibilityLabel="Close">
        <Animated.View
          style={{
            width: "100%",
            maxWidth: 420,
            opacity: enter,
            transform: [
              { scale: enter.interpolate({ inputRange: [0, 1], outputRange: [0.94, 1] }) },
            ],
          }}
        >
          {/* Stops a tap inside the sheet from reaching the scrim behind it. */}
          <Pressable
            onPress={() => {}}
            style={[
              styles.sheet,
              { backgroundColor: c.card, borderColor: c.border, borderRadius: radius.sheet },
              c.lift(2),
            ]}
          >
            <Text style={[T.heading, { color: c.foreground }]}>{title}</Text>
            {message ? (
              <Text style={[T.body, { color: c.mutedForeground, marginTop: space.sm }]}>
                {message}
              </Text>
            ) : null}
            <View style={styles.actions}>
              <Button
                label={confirmLabel}
                onPress={onConfirm}
                variant={tone === "danger" ? "danger" : "primary"}
                loading={loading}
              />
              <Button label={cancelLabel} onPress={onCancel} variant="quiet" disabled={loading} />
            </View>
          </Pressable>
        </Animated.View>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  scrim: {
    flex: 1,
    backgroundColor: "rgba(11, 30, 44, 0.55)",
    alignItems: "center",
    justifyContent: "center",
    padding: space.xl,
  },
  sheet: { borderWidth: 1, paddingHorizontal: space.lg, paddingTop: space.xl, paddingBottom: space.lg },
  actions: { marginTop: space.xl, gap: space.sm },
});
