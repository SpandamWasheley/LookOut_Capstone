import { Feather } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import React, { useState } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { GUTTER, radius, space, type as T } from "@/constants/theme";
import { useAuth } from "@/context/AuthContext";
import { useColors } from "@/hooks/useColors";
import * as api from "@/lib/api";

export default function ChangePasswordScreen() {
  const { completePasswordChange, logout, officer } = useAuth();
  const router = useRouter();
  const c = useColors();
  const insets = useSafeAreaInsets();

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    setError("");
    if (password.length < 8) {
      setError("Use at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("The two passwords don't match.");
      return;
    }

    setSubmitting(true);
    try {
      await api.changePassword(password);
      completePasswordChange();
      router.replace("/(tabs)");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save the new password. Try again.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={[styles.root, { backgroundColor: c.background }]}
      behavior={Platform.OS === "ios" ? "padding" : "height"}
    >
      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          { paddingTop: insets.top + 56, paddingBottom: insets.bottom + space.xxxl },
        ]}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.brand}>
          <View style={[styles.mark, { backgroundColor: c.primary }]}>
            <Feather name="shield" size={34} color={c.primaryForeground} />
          </View>
          <Text style={[T.title, styles.centred, { color: c.foreground }]}>
            Choose your password
          </Text>
          <Text style={[T.body, styles.centred, { color: c.mutedForeground }]}>
            Your account was set up with a temporary password. Pick your own before you carry on.
          </Text>
        </View>

        <View
          style={[
            styles.card,
            { backgroundColor: c.card, borderColor: c.border, borderRadius: radius.card },
            c.lift(1),
          ]}
        >
          <Field
            label="New password"
            icon="lock"
            placeholder="At least 8 characters"
            value={password}
            onChangeText={(v) => {
              setPassword(v);
              setError("");
            }}
            secret
            returnKeyType="next"
          />
          <Field
            label="Confirm new password"
            icon="lock"
            placeholder="Type it again"
            value={confirm}
            onChangeText={(v) => {
              setConfirm(v);
              setError("");
            }}
            secret
            returnKeyType="done"
            onSubmitEditing={handleSubmit}
          />

          {!!error && <Notice tone="error" message={error} />}

          <Button label="Save password" onPress={handleSubmit} loading={submitting} icon="check" />

          <Pressable
            onPress={() => logout()}
            accessibilityRole="button"
            hitSlop={8}
            style={({ pressed }) => [styles.signOut, { opacity: pressed ? 0.6 : 1 }]}
          >
            <Text style={[T.metaStrong, { color: c.mutedForeground }]}>
              {officer?.username ? `Not ${officer.username}? Sign out` : "Sign out"}
            </Text>
          </Pressable>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  scroll: { flexGrow: 1, paddingHorizontal: GUTTER, gap: space.xxl },
  brand: { alignItems: "center", gap: space.sm },
  mark: {
    width: 76,
    height: 76,
    borderRadius: radius.card + 4,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: space.xs,
  },
  centred: { textAlign: "center" },
  card: { borderWidth: 1, padding: space.xl, gap: space.base },
  signOut: { alignItems: "center", paddingVertical: space.sm },
});
