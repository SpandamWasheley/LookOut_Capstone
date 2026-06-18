import { Feather } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import React, { useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

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
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const handleSendCode = async () => {
    if (!email.trim()) return;
    setError("");
    setBusy(true);
    try {
      await api.sendForgotPasswordCode(email.trim());
      setStep(2);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send code.");
    } finally {
      setBusy(false);
    }
  };

  const handleReset = async () => {
    setError("");
    if (!code.trim()) { setError("Enter the code sent to your email."); return; }
    if (password.length < 8) { setError("Password must be at least 8 characters."); return; }
    if (password !== confirm) { setError("Passwords do not match."); return; }

    setBusy(true);
    try {
      await api.resetForgotPassword(email.trim(), code.trim(), password);
      setStep(3);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reset password.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={[styles.root, { backgroundColor: c.background }]}
      behavior={Platform.OS === "ios" ? "padding" : "height"}
    >
      <ScrollView
        contentContainerStyle={[styles.container, { paddingTop: insets.top + 60, paddingBottom: insets.bottom + 40 }]}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <Pressable onPress={() => router.back()} style={styles.backBtn}>
          <Feather name="arrow-left" size={20} color={c.foreground} />
        </Pressable>

        <View style={styles.brand}>
          <View style={[styles.logoWrap, { backgroundColor: c.primary }]}>
            <Feather name="key" size={32} color="#fff" />
          </View>
          <Text style={[styles.title, { color: c.foreground }]}>
            {step === 3 ? "Password reset" : "Forgot your password?"}
          </Text>
          <Text style={[styles.subtitle, { color: c.mutedForeground }]}>
            {step === 1 && "Enter the email you registered with — we'll send a reset code to it."}
            {step === 2 && `Enter the code sent to ${email}, then choose a new password.`}
            {step === 3 && "You can now sign in with your new password."}
          </Text>
        </View>

        {step === 1 && (
          <View style={[styles.card, { backgroundColor: c.card, borderColor: c.border }]}>
            <View>
              <Text style={[styles.label, { color: c.mutedForeground }]}>Email</Text>
              <View style={[styles.inputRow, { backgroundColor: c.muted, borderColor: c.border }]}>
                <Feather name="mail" size={16} color={c.mutedForeground} />
                <TextInput
                  style={[styles.input, { color: c.foreground }]}
                  placeholder="you@example.com"
                  placeholderTextColor={c.mutedForeground}
                  value={email}
                  onChangeText={(v) => { setEmail(v); setError(""); }}
                  autoCapitalize="none"
                  keyboardType="email-address"
                />
              </View>
            </View>

            {!!error && (
              <View style={[styles.errorBox, { backgroundColor: "#fed7d7" }]}>
                <Feather name="alert-circle" size={14} color={c.primary} />
                <Text style={[styles.errorText, { color: c.primary }]}>{error}</Text>
              </View>
            )}

            <Pressable
              style={({ pressed }) => [styles.submitBtn, { backgroundColor: c.primary, opacity: pressed || busy ? 0.8 : 1 }]}
              onPress={handleSendCode}
              disabled={busy}
            >
              {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.submitBtnText}>Send reset code</Text>}
            </Pressable>
          </View>
        )}

        {step === 2 && (
          <View style={[styles.card, { backgroundColor: c.card, borderColor: c.border }]}>
            <View>
              <Text style={[styles.label, { color: c.mutedForeground }]}>Reset code</Text>
              <View style={[styles.inputRow, { backgroundColor: c.muted, borderColor: c.border }]}>
                <Feather name="hash" size={16} color={c.mutedForeground} />
                <TextInput
                  style={[styles.input, { color: c.foreground, letterSpacing: 2 }]}
                  placeholder="6-digit code"
                  placeholderTextColor={c.mutedForeground}
                  value={code}
                  onChangeText={(v) => { setCode(v); setError(""); }}
                  keyboardType="number-pad"
                  maxLength={6}
                />
              </View>
            </View>

            <View>
              <Text style={[styles.label, { color: c.mutedForeground }]}>New password</Text>
              <View style={[styles.inputRow, { backgroundColor: c.muted, borderColor: c.border }]}>
                <Feather name="lock" size={16} color={c.mutedForeground} />
                <TextInput
                  style={[styles.input, { color: c.foreground }]}
                  placeholder="At least 8 characters"
                  placeholderTextColor={c.mutedForeground}
                  value={password}
                  onChangeText={(v) => { setPassword(v); setError(""); }}
                  secureTextEntry={!showPassword}
                />
                <Pressable onPress={() => setShowPassword((v) => !v)}>
                  <Feather name={showPassword ? "eye-off" : "eye"} size={16} color={c.mutedForeground} />
                </Pressable>
              </View>
            </View>

            <View>
              <Text style={[styles.label, { color: c.mutedForeground }]}>Confirm new password</Text>
              <View style={[styles.inputRow, { backgroundColor: c.muted, borderColor: c.border }]}>
                <Feather name="lock" size={16} color={c.mutedForeground} />
                <TextInput
                  style={[styles.input, { color: c.foreground }]}
                  placeholder="Re-enter new password"
                  placeholderTextColor={c.mutedForeground}
                  value={confirm}
                  onChangeText={(v) => { setConfirm(v); setError(""); }}
                  secureTextEntry={!showPassword}
                  onSubmitEditing={handleReset}
                />
              </View>
            </View>

            {!!error && (
              <View style={[styles.errorBox, { backgroundColor: "#fed7d7" }]}>
                <Feather name="alert-circle" size={14} color={c.primary} />
                <Text style={[styles.errorText, { color: c.primary }]}>{error}</Text>
              </View>
            )}

            <Pressable
              style={({ pressed }) => [styles.submitBtn, { backgroundColor: c.primary, opacity: pressed || busy ? 0.8 : 1 }]}
              onPress={handleReset}
              disabled={busy}
            >
              {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.submitBtnText}>Reset password</Text>}
            </Pressable>
          </View>
        )}

        {step === 3 && (
          <View style={[styles.card, { backgroundColor: c.card, borderColor: c.border, alignItems: "center" }]}>
            <View style={[styles.successCircle, { backgroundColor: c.successLight }]}>
              <Feather name="check-circle" size={28} color={c.success} />
            </View>
            <Pressable
              style={({ pressed }) => [styles.submitBtn, { backgroundColor: c.primary, opacity: pressed ? 0.8 : 1, width: "100%" }]}
              onPress={() => router.replace("/login")}
            >
              <Text style={styles.submitBtnText}>Back to login</Text>
            </Pressable>
          </View>
        )}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  container: { flexGrow: 1, paddingHorizontal: 24, gap: 24 },
  backBtn: { width: 36, height: 36, alignItems: "center", justifyContent: "center" },
  brand: { alignItems: "center", gap: 10 },
  logoWrap: { width: 72, height: 72, borderRadius: 22, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 20, fontFamily: "Inter_700Bold", textAlign: "center" },
  subtitle: { fontSize: 13, fontFamily: "Inter_400Regular", textAlign: "center", lineHeight: 19, paddingHorizontal: 8 },
  card: { borderRadius: 16, borderWidth: 1, padding: 24, gap: 18 },
  label: { fontSize: 12, fontFamily: "Inter_600SemiBold", marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.5 },
  inputRow: { flexDirection: "row", alignItems: "center", gap: 10, borderWidth: 1, borderRadius: 10, paddingHorizontal: 14, paddingVertical: 13 },
  input: { flex: 1, fontSize: 15, fontFamily: "Inter_400Regular", padding: 0 },
  errorBox: { flexDirection: "row", alignItems: "center", gap: 8, padding: 12, borderRadius: 10 },
  errorText: { fontSize: 13, fontFamily: "Inter_500Medium", flex: 1 },
  submitBtn: { alignItems: "center", justifyContent: "center", paddingVertical: 15, borderRadius: 12 },
  submitBtnText: { color: "#fff", fontSize: 16, fontFamily: "Inter_600SemiBold" },
  successCircle: { width: 64, height: 64, borderRadius: 32, alignItems: "center", justifyContent: "center", marginBottom: 8 },
});
