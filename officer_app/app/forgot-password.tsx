import { Feather } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import React, { useState } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { GUTTER, radius, space, TOUCH_MIN, type as T } from "@/constants/theme";
import { useColors } from "@/hooks/useColors";
import * as api from "@/lib/api";

export default function ForgotPasswordScreen() {
  const router = useRouter();
  const c = useColors();
  const insets = useSafeAreaInsets();

  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const handleSendCode = async () => {
    if (!email.trim()) return;
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      setError("That doesn't look like an email address.");
      return;
    }
    setError("");
    setBusy(true);
    try {
      await api.sendForgotPasswordCode(email.trim());
      setStep(2);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't send the code. Check your signal and try again.");
    } finally {
      setBusy(false);
    }
  };

  const handleReset = async () => {
    setError("");
    if (!code.trim()) {
      setError("Enter the code from your email.");
      return;
    }
    if (password.length < 8) {
      setError("Use at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("The two passwords don't match.");
      return;
    }

    setBusy(true);
    try {
      await api.resetForgotPassword(email.trim(), code.trim(), password);
      setStep(3);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't reset your password. Check the code and try again.");
    } finally {
      setBusy(false);
    }
  };

  const heading = step === 3 ? "Password changed" : "Reset your password";
  const blurb =
    step === 1
      ? "Enter the email your account was registered with. We'll send a six-digit code to it."
      : step === 2
        ? `Enter the code we sent to ${email}, then pick a new password.`
        : "Sign in with your new password.";

  return (
    <KeyboardAvoidingView
      style={[styles.root, { backgroundColor: c.background }]}
      behavior={Platform.OS === "ios" ? "padding" : "height"}
    >
      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          { paddingTop: insets.top + space.base, paddingBottom: insets.bottom + space.xxxl },
        ]}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <Pressable
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Go back"
          hitSlop={8}
          style={({ pressed }) => [styles.back, { opacity: pressed ? 0.6 : 1 }]}
        >
          <Feather name="arrow-left" size={20} color={c.foreground} />
        </Pressable>

        <View style={styles.brand}>
          <View style={[styles.mark, { backgroundColor: step === 3 ? c.closed : c.primary }]}>
            <Feather
              name={step === 3 ? "check" : "key"}
              size={30}
              color={step === 3 ? "#ffffff" : c.primaryForeground}
            />
          </View>
          <Text style={[T.title, styles.centred, { color: c.foreground }]}>{heading}</Text>
          <Text style={[T.body, styles.centred, { color: c.mutedForeground }]}>{blurb}</Text>
        </View>

        <View
          style={[
            styles.card,
            { backgroundColor: c.card, borderColor: c.border, borderRadius: radius.card },
            c.lift(1),
          ]}
        >
          {step === 1 && (
            <>
              <Field
                label="Email"
                icon="mail"
                placeholder="you@example.com"
                value={email}
                onChangeText={(v) => {
                  setEmail(v);
                  setError("");
                }}
                autoCapitalize="none"
                keyboardType="email-address"
                autoComplete="email"
              />
              {!!error && <Notice tone="error" message={error} />}
              <Button label="Send reset code" onPress={handleSendCode} loading={busy} icon="send" />
            </>
          )}

          {step === 2 && (
            <>
              <Field
                label="Reset code"
                icon="hash"
                placeholder="000000"
                value={code}
                onChangeText={(v) => {
                  setCode(v);
                  setError("");
                }}
                keyboardType="number-pad"
                maxLength={6}
                spaced
              />
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
                onSubmitEditing={handleReset}
              />
              {!!error && <Notice tone="error" message={error} />}
              <Button label="Change password" onPress={handleReset} loading={busy} icon="check" />
            </>
          )}

          {step === 3 && (
            /* This step used to be a green tick and a button, with nothing
               saying what had happened — the heading above was the only
               confirmation, and it scrolled off on a small phone. */
            <>
              <Notice tone="success" message="Your password has been changed." />
              <Button label="Go to sign in" onPress={() => router.replace("/login")} icon="log-in" />
            </>
          )}
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  scroll: { flexGrow: 1, paddingHorizontal: GUTTER, gap: space.xl },
  back: { width: TOUCH_MIN, height: TOUCH_MIN, alignItems: "flex-start", justifyContent: "center" },
  brand: { alignItems: "center", gap: space.sm },
  mark: {
    width: 68,
    height: 68,
    borderRadius: radius.card,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: space.xs,
  },
  centred: { textAlign: "center" },
  card: { borderWidth: 1, padding: space.xl, gap: space.base },
});
